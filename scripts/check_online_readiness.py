import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

REQUIRED_FOR_ONLINE = [
    "database_url",
    "anthropic_api_key",
    "github_token",
    "vercel_token",
]

RECOMMENDED_FOR_FULL_PIPELINE = [
    "apify_api_token",
    "resend_api_key",
]


def _present(value: str) -> bool:
    return bool((value or "").strip()) and "mock" not in (value or "").strip().lower()



def main() -> int:
    print("Online readiness check")
    print("=" * 24)
    print(f"Output dir: {Path(settings.output_dir).resolve()}")
    print(f"Mock mode: {settings.mock_mode}")
    print(f"Tools mock mode: {settings.tools_mock_mode}")
    print(f"QA mock: {settings.qa_mock}")
    print(f"Builder mock: {settings.builder_mock}")
    print(f"Auto interval: {settings.auto_pipeline_interval_minutes} minute(s)")

    missing_required = [name for name in REQUIRED_FOR_ONLINE if not _present(getattr(settings, name, ""))]
    missing_recommended = [name for name in RECOMMENDED_FOR_FULL_PIPELINE if not _present(getattr(settings, name, ""))]

    if missing_required:
        print("\nMissing required settings for online deployment:")
        for item in missing_required:
            print(f"- {item}")
    else:
        print("\nRequired production settings look present.")

    if missing_recommended:
        print("\nMissing recommended settings for a full live pipeline:")
        for item in missing_recommended:
            print(f"- {item}")
    else:
        print("\nRecommended live-service settings look present.")

    if settings.mock_mode or settings.tools_mock_mode or settings.qa_mock or settings.builder_mock:
        print("\nWarning: one or more mock flags are still enabled.")
        print("Set MOCK_MODE=false, TOOLS_MOCK_MODE=false, QA_MOCK=false, BUILDER_MOCK=false for production.")
    else:
        print("\nAll major mock flags are disabled.")

    return 1 if missing_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
