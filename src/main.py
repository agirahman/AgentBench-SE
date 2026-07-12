from config import Config
from utils.logger import logger


def main():

    logger.info("AgentBench-SE started")

    logger.info(f"Model : {Config.GEMINI_MODEL}")

    if Config.GEMINI_API_KEY:
        logger.success("Gemini API Key Loaded")
    else:
        logger.error("Gemini API Key Not Found")


if __name__ == "__main__":
    main()  