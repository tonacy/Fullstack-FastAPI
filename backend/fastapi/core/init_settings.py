import argparse
from backend.fastapi.core.config import get_settings

# Set up the argument parser
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["dev", "prod"], default="dev", help="Set the running mode")
parser.add_argument("--host", type=str, default="127.0.0.1", help="Set the host")

# Only parse arguments if the script is run directly
if __name__ == "__main__":
    args = parser.parse_args()
else:
    # Provide default values or mock args when imported
    args = argparse.Namespace(mode="dev", host="127.0.0.1")

# Initialize and update settings
settings = get_settings(args.mode)

# Save updated settings for import in other modules
global_settings = settings
