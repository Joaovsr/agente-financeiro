"""Converte o contêiner OGG/Opus do Telegram em WebM/Opus para transcrição."""

import asyncio

from imageio_ffmpeg import get_ffmpeg_exe

from .service import DomainError


async def ogg_to_webm(audio: bytes) -> bytes:
    if not audio.startswith(b"OggS"):
        raise DomainError("Áudio de voz inválido")
    try:
        executable = await asyncio.to_thread(get_ffmpeg_exe)
        process = await asyncio.create_subprocess_exec(
            executable,
            "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "ogg", "-i", "pipe:0", "-map", "0:a:0",
            "-c:a", "copy", "-f", "webm", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, RuntimeError) as exc:
        raise DomainError("Conversor de áudio indisponível") from exc
    try:
        converted, _ = await asyncio.wait_for(process.communicate(audio), timeout=90)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise DomainError("Conversão do áudio demorou demais") from exc
    if process.returncode != 0 or not converted.startswith(b"\x1a\x45\xdf\xa3"):
        raise DomainError("Não foi possível converter o áudio de voz")
    if len(converted) > 25 * 1024 * 1024:
        raise DomainError("Áudio convertido acima de 25 MB")
    return converted
