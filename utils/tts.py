"""
TTS 语音合成模块 —— 基于 edge-tts (微软语音, 免费, 中文自然)
用法:
    from utils.tts import tts_generate
    audio_bytes = tts_generate("你好", voice="zh-CN-XiaoxiaoNeural")
"""
import asyncio
import io
import logging

logger = logging.getLogger(__name__)

# 中文语音列表 (edge-tts 支持)
VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",   # 女声, 活泼
    "xiaoyi": "zh-CN-XiaoyiNeural",       # 女声, 温柔
    "yunjian": "zh-CN-YunjianNeural",     # 男声, 沉稳
    "yunxi": "zh-CN-YunxiNeural",         # 男声, 年轻
    "yunyang": "zh-CN-YunyangNeural",     # 男声, 新闻
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def tts_generate(text: str, voice: str = DEFAULT_VOICE,
                 rate: str = "+0%", pitch: str = "+0Hz",
                 max_retries: int = 2) -> bytes:
    """
    同步生成 TTS 音频 (mp3 格式)

    Args:
        text: 要合成的文本
        voice: 语音名称 (见 VOICES)
        rate: 语速, 如 "+0%", "-10%", "+20%"
        pitch: 音调, 如 "+0Hz", "+50Hz"
        max_retries: 网络失败重试次数

    Returns:
        mp3 音频字节
    """
    if not text or not text.strip():
        return b""

    try:
        import edge_tts
    except ImportError:
        logger.warning("edge-tts 未安装, TTS 不可用")
        return b""

    async def _generate():
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # 用独立事件循环, 避免与 Flask/其他框架的事件循环冲突
            loop = asyncio.new_event_loop()
            try:
                audio = loop.run_until_complete(_generate())
            finally:
                loop.close()
            if audio:
                logger.info("TTS 生成完成: %d 字 -> %d 字节 (尝试 %d)",
                            len(text), len(audio), attempt + 1)
                return audio
        except Exception as e:
            last_error = e
            logger.warning("TTS 生成失败 (尝试 %d/%d): %s",
                           attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                import time
                time.sleep(1)

    logger.error("TTS 生成最终失败: %s", last_error)
    return b""


def list_voices() -> dict:
    """返回可用语音列表"""
    return VOICES
