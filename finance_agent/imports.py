"""Importação assistida: extrai propostas, revisa e só então confirma."""

import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from .models import ImportBatch, Movement, Occurrence, Schedule, Wallet
from .service import DomainError, FinanceService, bounded_text, money, optional_category, similar_description, today_local


class ExtractedItem(BaseModel):
    occurred_on: str | None
    description: str
    amount: str | None
    direction: str
    wallet_name: str | None
    category: str | None
    uncertain: bool


class Extraction(BaseModel):
    items: list[ExtractedItem]


class ImportService:
    def __init__(self, finance: FinanceService):
        self.finance = finance

    def by_hash(self, file_hash: str) -> dict | None:
        with self.finance.factory() as session:
            batch = session.scalar(select(ImportBatch).where(ImportBatch.owner_id == self.finance.owner_id, ImportBatch.file_sha256 == file_hash))
            return self._view(batch) if batch else None

    def _view(self, batch: ImportBatch) -> dict:
        return {"id": batch.id, "filename": batch.filename, "status": batch.status, "version": batch.version, "items": batch.proposals, "result": batch.result}

    def list_batches(self, limit: int = 10) -> list[dict]:
        with self.finance.factory() as session:
            rows = session.scalars(select(ImportBatch).where(ImportBatch.owner_id == self.finance.owner_id).order_by(ImportBatch.created_at.desc()).limit(limit)).all()
            return [self._view(row) for row in rows]

    def draft_count(self) -> int:
        with self.finance.factory() as session:
            return session.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.owner_id == self.finance.owner_id, ImportBatch.status == "draft")) or 0

    def get_batch(self, batch_id: str) -> dict:
        with self.finance.factory() as session:
            batch = session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.owner_id == self.finance.owner_id))
            if batch is None:
                raise DomainError("Lote não encontrado")
            return self._view(batch)

    def _candidates(self, session, wallet_id: str | None, occurred_on: date | None, amount: Decimal | None, kind: str | None, description: str = "") -> tuple[list[str], list[str]]:
        if not all((wallet_id, occurred_on, amount, kind)):
            return [], []
        if occurred_on > today_local() + timedelta(days=3650):
            return [], []
        self.finance._ensure_occurrences(session, occurred_on + timedelta(days=3))
        existing = session.scalars(select(Movement).where(Movement.owner_id == self.finance.owner_id, Movement.wallet_id == wallet_id, Movement.kind == kind, Movement.amount == amount, Movement.occurred_on.between(occurred_on - timedelta(days=2), occurred_on + timedelta(days=2)), Movement.voided_at.is_(None))).all()
        forecast = session.execute(select(Occurrence, Schedule).join(Schedule, Occurrence.schedule_id == Schedule.id).where(Schedule.owner_id == self.finance.owner_id, Schedule.wallet_id == wallet_id, Schedule.kind == kind, Occurrence.status == "pending", Occurrence.due_on.between(occurred_on - timedelta(days=3), occurred_on + timedelta(days=3)))).all()
        matching = [row.id for row, rule in forecast if row.due_on == occurred_on or rule.amount == amount or similar_description(description, rule.description)]
        return [row.id for row in existing], matching

    def create_batch(self, data: bytes, filename: str, content_type: str, extraction: Extraction) -> dict:
        if not data or len(data) > 10 * 1024 * 1024:
            raise DomainError("Documento vazio ou acima de 10 MB")
        if len(extraction.items) > 100:
            raise DomainError("Documento com mais de 100 itens; divida a importação")
        file_hash = hashlib.sha256(data).hexdigest()
        with self.finance.factory.begin() as session:
            existing = session.scalar(select(ImportBatch).where(ImportBatch.owner_id == self.finance.owner_id, ImportBatch.file_sha256 == file_hash))
            if existing:
                return self._view(existing)
            wallets = session.scalars(select(Wallet).where(Wallet.owner_id == self.finance.owner_id, Wallet.active.is_(True))).all()
            by_name = {w.name.casefold(): w.id for w in wallets}
            proposals = []
            seen = set()
            for item in extraction.items:
                kind = item.direction if item.direction in {"income", "expense"} else None
                try:
                    day = date.fromisoformat(item.occurred_on) if item.occurred_on else None
                except ValueError:
                    day = None
                try:
                    amount = money(item.amount) if item.amount else None
                except DomainError:
                    amount = None
                wallet_id = by_name.get(item.wallet_name.casefold()) if item.wallet_name else (wallets[0].id if len(wallets) == 1 else None)
                duplicate_ids, occurrence_ids = self._candidates(session, wallet_id, day, amount, kind, item.description)
                identity = (wallet_id, day, amount, kind, item.description.strip().casefold())
                repeated_in_file = identity in seen
                seen.add(identity)
                proposals.append({"id": uuid4().hex, "occurred_on": day.isoformat() if day else None, "description": item.description.strip()[:300], "amount": str(amount) if amount else None, "kind": kind, "wallet_id": wallet_id, "category": item.category, "uncertain": item.uncertain or not all((day, amount, kind, wallet_id)), "duplicate_movement_ids": duplicate_ids, "matching_occurrence_ids": occurrence_ids, "repeated_in_file": repeated_in_file, "action": "include", "occurrence_id": None, "allow_duplicate": False, "allow_unlinked": False})
            batch = ImportBatch(owner_id=self.finance.owner_id, file_sha256=file_hash, filename=filename[:255], content_type=content_type, proposals=proposals)
            session.add(batch)
            session.flush()
            return self._view(batch)

    def edit_item(self, batch_id: str, item_id: str, *, version: int, patch: dict, key: str) -> dict:
        payload = {"action": "edit_import_item", "batch_id": batch_id, "item_id": item_id, "version": version, "patch": patch}
        with self.finance.factory.begin() as session:
            if previous := self.finance._command(session, key, payload):
                return previous
            batch = session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.owner_id == self.finance.owner_id))
            if batch is None or batch.status != "draft" or batch.version != version:
                raise DomainError("Lote não encontrado, confirmado ou alterado; consulte a versão atual")
            items = [dict(item) for item in batch.proposals]
            found = next((item for item in items if item["id"] == item_id), None)
            if found is None:
                raise DomainError("Item não encontrado")
            for field in ("occurred_on", "description", "amount", "kind", "wallet_id", "category", "action", "occurrence_id", "allow_duplicate", "allow_unlinked"):
                if field in patch and patch[field] is not None:
                    found[field] = patch[field]
            if patch.get("action") in {"include", "ignore"}:
                found["occurrence_id"] = None
            elif patch.get("action") == "link":
                found["allow_unlinked"] = False
            if found["action"] not in {"include", "ignore", "link"}:
                raise DomainError("Ação inválida")
            if found["kind"] not in {"income", "expense", None}:
                raise DomainError("Direção inválida")
            if found["description"]:
                found["description"] = bounded_text(found["description"], 300, "Descrição")
            found["category"] = optional_category(found["category"])
            if found["occurred_on"]:
                found["occurred_on"] = date.fromisoformat(found["occurred_on"]).isoformat()
            if found["amount"]:
                found["amount"] = str(money(found["amount"]))
            if found["wallet_id"]:
                self.finance._wallet(session, found["wallet_id"])
            if found["action"] == "link" and not found["occurrence_id"]:
                raise DomainError("Informe a ocorrência para vincular")
            batch.proposals = items
            flag_modified(batch, "proposals")
            batch.version += 1
            return self.finance._save_command(session, key, payload, self._view(batch))

    def confirm(self, batch_id: str, *, version: int, selected_ids: list[str], key: str) -> dict:
        payload = {"action": "confirm_import", "batch_id": batch_id, "version": version, "selected_ids": sorted(selected_ids)}
        with self.finance.factory.begin() as session:
            if previous := self.finance._command(session, key, payload):
                return previous
            batch = session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.owner_id == self.finance.owner_id))
            if batch is None or batch.status != "draft" or batch.version != version:
                raise DomainError("Lote não encontrado, confirmado ou alterado; consulte a versão atual")
            if len(set(selected_ids)) != len(selected_ids):
                raise DomainError("Item repetido na seleção")
            selected = [item for item in batch.proposals if item["id"] in selected_ids]
            if len(selected) != len(selected_ids):
                raise DomainError("Seleção contém item desconhecido")
            created = []
            ignored = []
            for item in selected:
                if item["action"] == "ignore":
                    ignored.append(item["id"])
                    continue
                if not all((item["occurred_on"], item["amount"], item["kind"], item["wallet_id"], item["description"])):
                    raise DomainError(f"Item {item['id']} incompleto")
                description = bounded_text(item["description"], 300, "Descrição")
                category = optional_category(item["category"])
                day = date.fromisoformat(item["occurred_on"])
                if day > today_local():
                    raise DomainError("Documento contém movimento realizado no futuro")
                value = money(item["amount"])
                wallet = self.finance._wallet(session, item["wallet_id"])
                if day < wallet.opened_on:
                    raise DomainError("Item anterior à abertura da carteira")
                duplicate_ids, occurrence_ids = self._candidates(session, wallet.id, day, value, item["kind"], item["description"])
                if duplicate_ids and not item["allow_duplicate"]:
                    raise DomainError(f"Item {item['id']} parece duplicado; revise o lote")
                if item["repeated_in_file"] and not item["allow_duplicate"]:
                    raise DomainError(f"Item {item['id']} está repetido no documento")
                occurrence_id = item["occurrence_id"]
                if occurrence_id:
                    occurrence = session.get(Occurrence, occurrence_id)
                    schedule = session.get(Schedule, occurrence.schedule_id) if occurrence else None
                    if schedule is None or schedule.owner_id != self.finance.owner_id or occurrence.status != "pending" or schedule.wallet_id != wallet.id or schedule.kind != item["kind"]:
                        raise DomainError("Ocorrência incompatível ou indisponível")
                    occurrence.status = "confirmed"
                    occurrence.actual_amount = value
                elif occurrence_ids and not item["allow_unlinked"]:
                    raise DomainError(f"Item {item['id']} corresponde a uma programação; escolha vínculo ou lançamento separado")
                movement = Movement(owner_id=self.finance.owner_id, kind=item["kind"], wallet_id=wallet.id, occurrence_id=occurrence_id, amount=value, occurred_on=day, description=description, category=category or ("Outros" if item["kind"] == "expense" else None), source="import")
                session.add(movement)
                session.flush()
                created.append(self.finance._movement_dict(movement))
            batch.status = "confirmed"
            batch.version += 1
            batch.result = {"created": created, "ignored": ignored, "not_selected": [item["id"] for item in batch.proposals if item["id"] not in selected_ids]}
            return self.finance._save_command(session, key, payload, self._view(batch))
