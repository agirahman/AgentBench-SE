from loguru import logger

logger.add(
    "logs/agentbench.log",
    rotation="5 MB",
    level="INFO",
)

__all__ = ["logger"]