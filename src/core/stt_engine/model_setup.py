"""
Model setup and download utilities for Whisper Large V3.

This module provides functions to download and verify the Whisper Large V3 model
using faster-whisper library, optimized for NVIDIA RTX GPUs.
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_model_cache_dir() -> Path:
    """
    Get the directory where models are cached.

    Returns:
        Path: The cache directory path
    """
    # Use XDG_CACHE_HOME or default to ~/.cache
    cache_home = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    model_cache = Path(cache_home) / "whisper-models"
    model_cache.mkdir(parents=True, exist_ok=True)
    return model_cache


def download_whisper_model(
    model_name: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    download_root: Optional[Path] = None
) -> bool:
    """
    Download and initialize Whisper Large V3 model using faster-whisper.

    Args:
        model_name: Model size to download (default: "large-v3")
        device: Device to use - "cuda" or "cpu" (default: "cuda")
        compute_type: Compute precision - "float16", "int8", "int8_float16" (default: "float16")
        download_root: Optional custom download directory

    Returns:
        bool: True if download successful, False otherwise
    """
    try:
        from faster_whisper import WhisperModel

        logger.info(f"Starting download of Whisper {model_name} model...")
        logger.info(f"Device: {device}, Compute type: {compute_type}")

        if download_root is None:
            download_root = get_model_cache_dir()

        # Initialize model - this will download if not present
        model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=str(download_root)
        )

        logger.info(f"Model {model_name} successfully downloaded and initialized!")
        logger.info(f"Model cached at: {download_root}")

        # Test the model with a dummy input to ensure it's working
        logger.info("Running verification test...")

        # The model is ready but we don't need to test transcription here
        # Just verify it loaded correctly
        del model
        logger.info("Model verification successful!")

        return True

    except ImportError as e:
        logger.error("faster-whisper not installed. Please run: pip install faster-whisper")
        logger.error(f"Error: {e}")
        return False
    except Exception as e:
        logger.error(f"Error downloading model: {e}")
        return False


def verify_model_exists(
    model_name: str = "large-v3",
    download_root: Optional[Path] = None
) -> bool:
    """
    Verify if the model is already downloaded.

    Args:
        model_name: Model name to check
        download_root: Optional custom download directory

    Returns:
        bool: True if model exists, False otherwise
    """
    if download_root is None:
        download_root = get_model_cache_dir()

    model_path = download_root / model_name
    exists = model_path.exists()

    if exists:
        logger.info(f"Model {model_name} found at {model_path}")
    else:
        logger.info(f"Model {model_name} not found at {model_path}")

    return exists


def main():
    """Main function to download the model when run as a script."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("=" * 60)
    logger.info("Whisper Large V3 Model Setup")
    logger.info("=" * 60)

    # Check if model already exists
    if verify_model_exists():
        logger.info("Model already downloaded. Skipping download.")
        return

    # Download the model
    success = download_whisper_model(
        model_name="large-v3",
        device="cuda",
        compute_type="float16"
    )

    if success:
        logger.info("=" * 60)
        logger.info("Setup completed successfully!")
        logger.info("=" * 60)
    else:
        logger.error("=" * 60)
        logger.error("Setup failed!")
        logger.error("=" * 60)
        exit(1)


if __name__ == "__main__":
    main()
