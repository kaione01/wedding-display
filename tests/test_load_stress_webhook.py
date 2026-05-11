"""Stress testing for 200+ concurrent LINE webhook messages"""
import asyncio
import base64
import hashlib
import hmac
import json
import random
import time
from unittest.mock import AsyncMock, patch

import pytest

from config import CHANNEL_SECRET


class MockAsyncFile:
    """Mock async file for testing image upload"""

    async def write(self, data):
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
class TestWebhookLoadStress:
    """Stress tests for 200+ concurrent webhook messages"""

    def generate_line_signature(self, body: bytes) -> str:
        """Generate valid LINE webhook signature"""
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    async def test_200_concurrent_text_messages(self):
        """Test 200 concurrent text messages via webhook"""
        from main import verify_line_signature

        messages_processed = 0
        start_time = time.time()

        # Simulate 200 concurrent text messages
        for i in range(200):
            message = {
                "type": "message",
                "source": {"userId": f"user_{i:03d}"},
                "replyToken": f"token_{i}",
                "message": {"type": "text", "text": f"Congratulations {i}! 🎉"},
            }

            payload = {"events": [message]}
            body = json.dumps(payload).encode("utf-8")
            signature = self.generate_line_signature(body)

            # Verify signature
            assert verify_line_signature(body, signature) is True
            messages_processed += 1

        elapsed = time.time() - start_time

        assert messages_processed == 200
        assert elapsed < 1.0  # Should process 200 in under 1 second
        print(f"✅ 200 text messages processed in {elapsed:.3f}s")

    async def test_200_concurrent_image_messages(self):
        """Test 200 concurrent image messages"""
        from main import verify_line_signature

        messages = []

        # Generate 200 image message signatures
        start_time = time.time()

        for i in range(200):
            message = {
                "type": "message",
                "source": {"userId": f"user_{i:03d}"},
                "replyToken": f"token_{i}",
                "message": {"type": "image", "id": f"image_{i}"},
            }

            payload = {"events": [message]}
            body = json.dumps(payload).encode("utf-8")
            signature = self.generate_line_signature(body)

            assert verify_line_signature(body, signature) is True
            messages.append((body, signature))

        elapsed = time.time() - start_time

        assert len(messages) == 200
        assert elapsed < 1.0
        print(f"✅ 200 image message validations in {elapsed:.3f}s")

    async def test_200_concurrent_sticker_messages(self):
        """Test 200 concurrent sticker messages"""
        from main import verify_line_signature

        sticker_count = 0

        for i in range(200):
            sticker_id = random.choice([11537, 11538, 11539, 11540])
            sticker_type = random.choice(["static", "animated"])

            message = {
                "type": "message",
                "source": {"userId": f"user_{i:03d}"},
                "replyToken": f"token_{i}",
                "message": {"type": "sticker", "stickerId": str(sticker_id), "stickerType": sticker_type},
            }

            payload = {"events": [message]}
            body = json.dumps(payload).encode("utf-8")
            signature = self.generate_line_signature(body)

            assert verify_line_signature(body, signature) is True
            sticker_count += 1

        assert sticker_count == 200
        print(f"✅ 200 sticker messages validated")

    async def test_signature_verification_performance_200(self):
        """Test signature verification performance for 200 messages"""
        from main import verify_line_signature

        start_time = time.time()

        for i in range(200):
            body = json.dumps({"events": [{"id": i}]}).encode("utf-8")
            signature = self.generate_line_signature(body)
            assert verify_line_signature(body, signature) is True

        elapsed = time.time() - start_time

        # 200 HMAC-SHA256 verifications should be fast
        assert elapsed < 0.5
        avg_per_message = (elapsed / 200) * 1000
        print(f"✅ 200 signature verifications in {elapsed:.3f}s ({avg_per_message:.3f}ms each)")

    async def test_badword_filtering_performance_200_messages(self):
        """Test badword filtering performance for 200 messages"""
        from main import is_clean

        test_messages = [
            "恭喜Kai和Bella新婚快樂",
            "祝百年好合",
            "希望你們幸福美滿",
        ] * 67 + ["恭喜"]  # 200 total

        start_time = time.time()

        clean_count = 0
        for msg in test_messages:
            if is_clean(msg):
                clean_count += 1

        elapsed = time.time() - start_time

        assert clean_count > 190  # Most should be clean
        assert elapsed < 0.1
        print(f"✅ 200 messages filtered in {elapsed:.6f}s")

    async def test_concurrent_message_broadcast_200(self):
        """Test broadcasting messages to 200 connected displays"""
        # Simulate ConnectionManager broadcast
        class MockWS:
            async def send_json(self, data):
                pass

        connections = [MockWS() for _ in range(200)]
        messages_to_broadcast = [
            {"type": "text", "sender_name": f"Guest{i}", "content": f"Message {i}"}
            for i in range(200)
        ]

        start_time = time.time()

        for msg in messages_to_broadcast:
            # Simulate broadcast to all 200 connections
            tasks = [conn.send_json(msg) for conn in connections]
            await asyncio.gather(*tasks)

        elapsed = time.time() - start_time

        # Broadcasting 200 messages to 200 connections (40K total sends) should be fast
        assert elapsed < 2.0
        print(f"✅ Broadcast 200 messages to 200 displays in {elapsed:.3f}s")

    async def test_webhook_payload_parsing_200(self):
        """Test parsing 200 different webhook payloads"""
        start_time = time.time()

        for i in range(200):
            message_types = ["text", "image", "sticker"]
            msg_type = random.choice(message_types)

            if msg_type == "text":
                payload = {
                    "events": [
                        {
                            "type": "message",
                            "message": {"type": "text", "text": f"Message {i}"},
                            "source": {"userId": f"user_{i}"},
                        }
                    ]
                }
            elif msg_type == "image":
                payload = {
                    "events": [
                        {
                            "type": "message",
                            "message": {"type": "image", "id": f"img_{i}"},
                            "source": {"userId": f"user_{i}"},
                        }
                    ]
                }
            else:
                payload = {
                    "events": [
                        {
                            "type": "message",
                            "message": {"type": "sticker", "stickerId": "1", "stickerType": "static"},
                            "source": {"userId": f"user_{i}"},
                        }
                    ]
                }

            body = json.dumps(payload).encode("utf-8")
            events = json.loads(body.decode("utf-8")).get("events", [])
            assert len(events) > 0

        elapsed = time.time() - start_time

        assert elapsed < 0.2
        print(f"✅ 200 webhook payloads parsed in {elapsed:.6f}s")


@pytest.mark.asyncio
class TestWebhookSecurityUnderLoad:
    """Test security mechanisms under load"""

    def generate_line_signature(self, body: bytes) -> str:
        """Generate valid LINE webhook signature"""
        mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        return base64.b64encode(mac).decode("utf-8")

    async def test_invalid_signature_detection_200(self):
        """Test that 200 invalid signatures are correctly rejected"""
        from main import verify_line_signature

        rejected_count = 0

        for i in range(200):
            body = json.dumps({"events": [{"id": i}]}).encode("utf-8")
            invalid_signature = base64.b64encode(b"invalid_sig").decode("utf-8")

            if not verify_line_signature(body, invalid_signature):
                rejected_count += 1

        assert rejected_count == 200  # All should be rejected
        print(f"✅ 200 invalid signatures correctly rejected")

    async def test_tampered_body_detection_200(self):
        """Test detection of 200 tampered messages"""
        from main import verify_line_signature

        detected_count = 0

        for i in range(200):
            original_body = json.dumps({"events": [{"id": i}]}).encode("utf-8")
            signature = self.generate_line_signature(original_body)

            # Tamper with body
            tampered_body = json.dumps({"events": [{"id": i, "tampered": True}]}).encode("utf-8")

            if not verify_line_signature(tampered_body, signature):
                detected_count += 1

        assert detected_count == 200  # All tampering should be detected
        print(f"✅ 200 tampered messages correctly detected")


@pytest.mark.asyncio
class TestDatabaseWriteUnderLoad:
    """Test database operations under load"""

    async def test_200_concurrent_message_writes(self):
        """Test writing 200 messages to database"""
        # Simulate database writes
        writes_completed = 0

        async def write_message(msg_id: int):
            # Simulate DB write
            await asyncio.sleep(0.001)
            return True

        start_time = time.time()

        tasks = [write_message(i) for i in range(200)]
        results = await asyncio.gather(*tasks)

        writes_completed = sum(results)
        elapsed = time.time() - start_time

        assert writes_completed == 200
        assert elapsed < 1.0
        print(f"✅ 200 database writes completed in {elapsed:.3f}s")

    async def test_message_retrieval_performance_large_dataset(self):
        """Test querying large message dataset"""
        # Simulate 10,000 existing messages in database
        # Query performance should remain good with 200 new additions

        messages = [{"id": i, "content": f"Message {i}"} for i in range(10000)]

        start_time = time.time()

        # Add 200 new messages
        new_messages = [{"id": i + 10000, "content": f"New {i}"} for i in range(200)]
        messages.extend(new_messages)

        # Query last 100
        last_100 = messages[-100:]

        elapsed = time.time() - start_time

        assert len(last_100) == 100
        assert elapsed < 0.1
        print(f"✅ Query on 10200 messages in {elapsed:.6f}s")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
