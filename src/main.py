from providers.groq_provider import GroqProvider
from providers.gemini_provider import GeminiProvider
from strategies.direct_strategy import DirectStrategy
from utils.logger import logger

from models.issue import Issue


def main():

    provider = GeminiProvider()

    if not provider.health_check():
        logger.error("Health check gagal. Hentikan program.")
        return

    strategy = DirectStrategy(provider)

    issue = Issue(
        title="Login button is not clickable",
        description="""
The login button does not respond after
the user enters username and password.
"""
    )

    patch = strategy.run(issue)

    print("=" * 60)
    # print(patch.content)
    print(patch.response)
    print("=" * 60)


if __name__ == "__main__":
    main()