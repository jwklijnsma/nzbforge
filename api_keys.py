from flask import Blueprint, jsonify
import secrets

class ApiKeyManager:
    @staticmethod
    def generate_key():
        return secrets.token_hex(32)

api_keys_bp = Blueprint('api_keys', __name__)

@api_keys_bp.route('/api/generate-key', methods=['POST'])
def generate_api_key():
    api_key = ApiKeyManager.generate_key()
    return jsonify({'api_key': api_key}), 201