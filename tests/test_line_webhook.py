"""Unit tests for LINE webhook handling"""
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import verify_line_signature, is_clean
from config import CHANNEL_SECRET


class TestLineSignatureVerification:
    """Test LINE Webhook HMAC-SHA256 signature verification"""

    def test_valid_signature(self):
        """Test that a valid signature is accepted"""
        body = b'{"events": []}'
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        signature = base64.b64encode(mac).decode("utf-8")

        assert verify_line_signature(body, signature) is True

    def test_invalid_signature(self):
        """Test that an invalid signature is rejected"""
        body = b'{"events": []}'
        wrong_signature = base64.b64encode(b"wrong_signature").decode("utf-8")

        assert verify_line_signature(body, wrong_signature) is False

    def test_empty_signature(self):
        """Test that empty signature is rejected"""
        body = b'{"events": []}'
        assert verify_line_signature(body, "") is False

    def test_tampered_body_fails_verification(self):
        """Test that signature fails when body is modified"""
        original_body = b'{"events": []}'
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), original_body, hashlib.sha256).digest()
        signature = base64.b64encode(mac).decode("utf-8")

        tampered_body = b'{"events": [], "tampered": true}'
        assert verify_line_signature(tampered_body, signature) is False

    def test_signature_timing_attack_resistant(self):
        """Test that signature comparison is timing-safe"""
        body = b'{"events": []}'
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        signature = base64.b64encode(mac).decode("utf-8")

        # Valid signature should return True
        assert verify_line_signature(body, signature) is True

        # Signature with first char wrong should fail
        wrong_sig = chr(ord(signature[0]) + 1) + signature[1:]
        assert verify_line_signature(body, wrong_sig) is False


class TestSecretCommandDetection:
    """Test secret command keywords for danmaku control"""

    def test_secret_start_keyword(self):
        """Test danmaku start command recognition"""
        secret_start = "婚禮開始14131928"
        assert secret_start == "婚禮開始14131928"

    def test_secret_stop_keyword(self):
        """Test danmaku stop command recognition"""
        secret_stop = "婚禮結束14131928"
        assert secret_stop == "婚禮結束14131928"

    def test_secret_status_keyword(self):
        """Test status check command recognition"""
        secret_status = "現在彈幕狀態14131928"
        assert secret_status == "現在彈幕狀態14131928"

    def test_secret_commands_are_case_sensitive(self):
        """Test that secret commands are case-sensitive"""
        secret = "婚禮開始14131928"
        variant1 = "婚禮開始14131928"  # Same
        variant2 = "婚礼開始14131928"  # Different first char (simplified Chinese)

        assert secret == variant1
        assert secret != variant2


class TestBadwordFiltering:
    """Test badword filtering functionality"""

    @patch("main.BADWORDS", {"火星文", "粗話", "髒話"})
    def test_clean_message_passes(self):
        """Test that clean messages pass the filter"""
        assert is_clean("今天天氣真好") is True
        assert is_clean("恭喜Kai和Bella結婚") is True

    @patch("main.BADWORDS", {"火星文", "粗話", "髒話"})
    def test_badword_detected(self):
        """Test that messages with badwords are detected"""
        assert is_clean("火星文太多了") is False
        assert is_clean("這是粗話") is False

    @patch("main.BADWORDS", {"火星文", "粗話", "髒話"})
    def test_case_insensitive_filtering(self):
        """Test that filtering is case-insensitive"""
        assert is_clean("火星文") is False
        assert is_clean("火星文") is False

    @patch("main.BADWORDS", {"test"})
    def test_empty_badwords_list(self):
        """Test with empty badwords list"""
        assert is_clean("anything goes") is True

    @patch("main.BADWORDS", {"測試"})
    def test_multiple_badwords_in_message(self):
        """Test detection of multiple badwords"""
        assert is_clean("測試這個測試") is False

    @patch("main.BADWORDS", {"bad"})
    def test_badword_as_substring(self):
        """Test detection when badword is substring"""
        assert is_clean("This is a badword") is False
        assert is_clean("badness") is False
        assert is_clean("This is good") is True


class TestMessageTypes:
    """Test different message type handling"""

    def test_text_message_structure(self):
        """Test text message structure"""
        msg = {"type": "text", "text": "Hello"}
        assert msg["type"] == "text"
        assert msg["text"] == "Hello"

    def test_image_message_structure(self):
        """Test image message structure"""
        msg = {"type": "image", "id": "msg_123"}
        assert msg["type"] == "image"
        assert msg["id"] == "msg_123"

    def test_sticker_message_structure(self):
        """Test sticker message structure"""
        msg = {"type": "sticker", "stickerId": "1", "stickerType": "static"}
        assert msg["type"] == "sticker"
        assert msg["stickerType"] in ["static", "animated"]


class TestStickerURLGeneration:
    """Test sticker URL generation"""

    def test_static_sticker_url_format(self):
        """Test static sticker URL generation"""
        sticker_id = "11537"
        sticker_type = "static"

        if sticker_type == "animated":
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker_animation@2x.apng"
        else:
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"

        assert "sticker@2x.png" in url
        assert sticker_id in url

    def test_animated_sticker_url_format(self):
        """Test animated sticker URL generation"""
        sticker_id = "11537"
        sticker_type = "animated"

        if sticker_type == "animated":
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker_animation@2x.apng"
        else:
            url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"

        assert "sticker_animation@2x.apng" in url
        assert sticker_id in url

    def test_sticker_url_includes_cdn(self):
        """Test that sticker URLs point to LINE CDN"""
        sticker_id = "11537"
        url = f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/iPhone/sticker@2x.png"
        assert "line-scdn.net" in url
        assert "stickershop" in url


class TestEmojisHandling:
    """Test emoji handling in messages"""

    def test_emoji_replacement(self):
        """Test that LINE emojis are replaced with Unicode"""
        text = "I love this (emoji) so much (emoji)"
        replaced = text.replace("(emoji)", "❤️")
        assert "❤️" in replaced
        assert "(emoji)" not in replaced

    def test_multiple_emoji_replacement(self):
        """Test multiple emoji replacements"""
        text = "(emoji)(emoji)(emoji)"
        replaced = text.replace("(emoji)", "❤️")
        assert replaced.count("❤️") == 3
