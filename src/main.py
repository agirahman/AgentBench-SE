from providers.gemini_provider import GeminiProvider
from utils.logger import logger


def main():
    logger.info("Starting AgentBench-SE...")

    provider = GeminiProvider()

    if provider.health_check():
        response = provider.generate(
            "Perkenalkan dirimu dalam satu kalimat."
        )

        print("\n===== RESPONSE =====")
        print(response)
    else:
        logger.error("Gemini tidak dapat diakses.")


if __name__ == "__main__":
    main()