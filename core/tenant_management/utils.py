# Copyright 2024 DIALOGIX
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import hashlib
from typing import Dict, Any

def obfuscate_api_key(api_key: str) -> str:
    """
    Obfuscate an API key for secure display.
    Shows first 6 and last 4 characters with asterisks in between.
    """
    if not api_key or len(api_key) < 10:
        return "***HIDDEN***"

    return f"{api_key[:6]}{'*' * (len(api_key) - 10)}{api_key[-4:]}"

def hash_api_key(api_key: str) -> str:
    """
    Create a SHA256 hash of the API key for audit purposes.
    """
    if not api_key:
        return "N/A"

    return f"sha256:{hashlib.sha256(api_key.encode()).hexdigest()[:12]}..."

def obfuscate_ai_settings(ai_settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obfuscate sensitive information in AI settings for admin display.
    """
    if not ai_settings:
        return {}

    # Create a copy to avoid modifying the original
    obfuscated = ai_settings.copy()

    # Obfuscate API key if present
    if 'api_key' in obfuscated and obfuscated['api_key']:
        obfuscated['api_key'] = obfuscate_api_key(obfuscated['api_key'])

    return obfuscated

def get_api_key_display_info(ai_settings: Dict[str, Any]) -> str:
    """
    Get a secure display string for API key information.
    """
    if not ai_settings or 'api_key' not in ai_settings:
        return "No API Key configured"

    api_key = ai_settings.get('api_key', '')
    if not api_key:
        return "No API Key configured"

    provider = ai_settings.get('provider', 'Unknown')
    obfuscated_key = obfuscate_api_key(api_key)
    key_hash = hash_api_key(api_key)

    return f"{provider.upper()}: {obfuscated_key} (Hash: {key_hash})"