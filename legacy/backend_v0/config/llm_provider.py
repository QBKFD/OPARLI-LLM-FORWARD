# backend/config/llm_provider.py
"""
LLM Provider Abstraction

Allows switching between different LLM providers (Gemini, Claude, OpenAI)
based on environment configuration.

Usage:
    from config.llm_provider import get_llm_client

    llm = get_llm_client()
    response = llm.generate("Analyze this chart pattern...")
"""

import os
import logging
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod
from enum import Enum

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers"""
    GEMINI = "gemini"
    CLAUDE = "claude"


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients"""

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> str:
        """Generate text completion"""
        pass

    @abstractmethod
    def generate_with_image(
        self,
        prompt: str,
        image_data: bytes,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        """Generate completion with image input (for chart analysis)"""
        pass

    @abstractmethod
    def generate_with_images(
        self,
        prompt: str,
        images: List[bytes],
        image_labels: List[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        """Generate completion with multiple images (for multi-timeframe analysis)"""
        pass

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict:
        """Generate structured JSON output"""
        pass


class GeminiClient(BaseLLMClient):
    """Google Gemini client"""

    def __init__(self, model: str = "gemini-1.5-pro", api_key: Optional[str] = None):
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable required")
        super().__init__(model, api_key)

        # Initialize Gemini
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        self.client = genai.GenerativeModel(self.model)
        logger.info(f"✓ Gemini client initialized (model: {self.model})")

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> str:
        import google.generativeai as genai

        # Combine system prompt with user prompt
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        response = self.client.generate_content(
            full_prompt,
            generation_config=generation_config
        )

        return response.text

    def generate_with_image(
        self,
        prompt: str,
        image_data: bytes,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        import google.generativeai as genai
        from PIL import Image
        import io

        # Load image
        image = Image.open(io.BytesIO(image_data))

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        generation_config = genai.GenerationConfig(
            temperature=temperature,
        )

        response = self.client.generate_content(
            [full_prompt, image],
            generation_config=generation_config
        )

        return response.text

    def generate_with_images(
        self,
        prompt: str,
        images: List[bytes],
        image_labels: List[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        """
        Generate completion with multiple images (for multi-timeframe chart analysis)

        Args:
            prompt: Text prompt
            images: List of image bytes (PNG/JPG)
            image_labels: Optional labels for each image (e.g., ['1min', '5min', '15min', '1H'])
            system_prompt: Optional system prompt
            temperature: Generation temperature

        Returns:
            Generated text response
        """
        import google.generativeai as genai
        from PIL import Image
        import io

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        # Build content list: prompt first, then all images
        content = [full_prompt]

        for i, img_bytes in enumerate(images):
            image = Image.open(io.BytesIO(img_bytes))
            content.append(image)

            # Add label after each image if provided
            if image_labels and i < len(image_labels):
                content.append(f"[Above: {image_labels[i]} chart]")

        generation_config = genai.GenerationConfig(
            temperature=temperature,
        )

        response = self.client.generate_content(
            content,
            generation_config=generation_config
        )

        return response.text

    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict:
        import json

        # Add JSON instruction to prompt
        json_prompt = f"{prompt}\n\nRespond with valid JSON matching this schema:\n{json.dumps(schema, indent=2)}"

        response = self.generate(
            json_prompt,
            system_prompt=system_prompt,
            **kwargs
        )

        # Parse JSON from response
        try:
            # Try to extract JSON from response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            return json.loads(response.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Gemini response: {e}")
            return {"error": "Failed to parse response", "raw": response}


class ClaudeClient(BaseLLMClient):
    """Anthropic Claude client"""

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: Optional[str] = None):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable required")
        super().__init__(model, api_key)

        # Initialize Claude
        import anthropic
        self.client = anthropic.Anthropic(api_key=self.api_key)
        logger.info(f"✓ Claude client initialized (model: {self.model})")

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs
    ) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt or "You are a helpful assistant.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return message.content[0].text

    def generate_with_image(
        self,
        prompt: str,
        image_data: bytes,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        import base64

        # Encode image to base64
        image_base64 = base64.standard_b64encode(image_data).decode("utf-8")

        # Detect media type (default to PNG)
        media_type = kwargs.get("media_type", "image/png")

        message = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=temperature,
            system=system_prompt or "You are a helpful assistant.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
        )

        return message.content[0].text

    def generate_with_images(
        self,
        prompt: str,
        images: List[bytes],
        image_labels: List[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> str:
        """
        Generate completion with multiple images (for multi-timeframe chart analysis)

        Args:
            prompt: Text prompt
            images: List of image bytes (PNG/JPG)
            image_labels: Optional labels for each image (e.g., ['1min', '5min', '15min', '1H'])
            system_prompt: Optional system prompt
            temperature: Generation temperature

        Returns:
            Generated text response
        """
        import base64

        # Detect media type (default to PNG)
        media_type = kwargs.get("media_type", "image/png")

        # Build content list with all images
        content = []

        for i, img_bytes in enumerate(images):
            image_base64 = base64.standard_b64encode(img_bytes).decode("utf-8")

            # Add image
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_base64,
                },
            })

            # Add label after each image if provided
            if image_labels and i < len(image_labels):
                content.append({
                    "type": "text",
                    "text": f"[Above: {image_labels[i]} chart]"
                })

        # Add the main prompt at the end
        content.append({
            "type": "text",
            "text": prompt
        })

        message = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=temperature,
            system=system_prompt or "You are a helpful assistant.",
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )

        return message.content[0].text

    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict:
        import json

        json_prompt = f"{prompt}\n\nRespond with valid JSON matching this schema:\n{json.dumps(schema, indent=2)}"

        response = self.generate(
            json_prompt,
            system_prompt=system_prompt,
            **kwargs
        )

        try:
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            return json.loads(response.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Claude response: {e}")
            return {"error": "Failed to parse response", "raw": response}


# ============================================================
# Provider Factory
# ============================================================

# Default models per provider
# Gemini 2.0 Flash is cost-effective for multimodal general-purpose tasks
DEFAULT_MODELS = {
    LLMProvider.GEMINI: "gemini-2.5-pro",
    LLMProvider.CLAUDE: "claude-sonnet-4-20250514",
}

# Client classes per provider
PROVIDER_CLIENTS = {
    LLMProvider.GEMINI: GeminiClient,
    LLMProvider.CLAUDE: ClaudeClient,
}

# Global client instance (singleton)
_llm_client: Optional[BaseLLMClient] = None


def get_llm_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    force_new: bool = False
) -> BaseLLMClient:
    """
    Get LLM client based on environment configuration

    Environment variables:
        LLM_PROVIDER: gemini, claude, or openai (default: gemini for test)
        LLM_MODEL: specific model to use (optional, uses provider default)
        ENV: development or production (affects default provider)

        GOOGLE_API_KEY: Required for Gemini
        ANTHROPIC_API_KEY: Required for Claude
        OPENAI_API_KEY: Required for OpenAI

    Args:
        provider: Override provider (gemini, claude, openai)
        model: Override model
        force_new: Create new instance instead of using singleton

    Returns:
        LLM client instance
    """
    global _llm_client

    if _llm_client is not None and not force_new:
        return _llm_client

    # Determine provider
    if provider is None:
        provider = os.getenv("LLM_PROVIDER")

        # Default based on environment
        if provider is None:
            env = os.getenv("ENV", "development")
            if env == "production":
                provider = "claude"  # Use Claude in production
            else:
                provider = "gemini"  # Use Gemini for testing (free credits)

    # Convert to enum
    try:
        provider_enum = LLMProvider(provider.lower())
    except ValueError:
        logger.warning(f"Unknown provider '{provider}', falling back to Gemini")
        provider_enum = LLMProvider.GEMINI

    # Determine model
    if model is None:
        model = os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider_enum]

    # Create client
    client_class = PROVIDER_CLIENTS[provider_enum]

    try:
        _llm_client = client_class(model=model)
        logger.info(f"✓ LLM Provider: {provider_enum.value} ({model})")
        return _llm_client
    except Exception as e:
        logger.error(f"Failed to initialize {provider_enum.value} client: {e}")
        raise


def get_provider_info() -> Dict[str, Any]:
    """Get current LLM provider configuration"""
    env = os.getenv("ENV", "development")
    provider = os.getenv("LLM_PROVIDER")

    if provider is None:
        provider = "claude" if env == "production" else "gemini"

    return {
        "environment": env,
        "provider": provider,
        "model": os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(LLMProvider(provider.lower())),
        "available_providers": [p.value for p in LLMProvider],
    }
