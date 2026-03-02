"""
Tests for image and document/file attachment handling.

Covers:
1. append_image_recap writes correct Obsidian embed format
2. append_file_recap writes correct embed format
3. Captions included when present
4. No-op when no active file
5. handle_photo downloads and saves image
6. handle_document downloads and saves file
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

SGT = timezone(timedelta(hours=8))
BOYANG_USER_ID = 411364623


def _make_update(user_id=BOYANG_USER_ID):
    update = MagicMock()
    update.message = MagicMock()
    update.message.from_user = MagicMock()
    update.message.from_user.id = user_id
    update.message.from_user.username = "b0yan913"
    update.message.from_user.first_name = "Boyang"
    update.message.reply_text = AsyncMock()
    update.message.caption = None
    return update


# ============================================================
# Recorder unit tests
# ============================================================

class TestAppendImageRecap:

    @pytest.fixture(autouse=True)
    def setup(self, digest_dir):
        import recorder
        from recorder import create_digest
        recorder._active_file = None
        now = datetime.now(SGT)
        create_digest(
            coverage_from=now - timedelta(hours=1),
            coverage_to=now,
            session_summaries=[{"session": "Test", "messages": 1, "summary": "Test."}],
        )

    def test_basic_image_embed(self):
        from recorder import append_image_recap, get_active_file
        result = append_image_recap("img-20260302-120000.jpg")
        assert result is True
        content = get_active_file().read_text()
        assert "![[img-20260302-120000.jpg]]" in content
        assert "📷" in content

    def test_image_with_caption(self):
        from recorder import append_image_recap, get_active_file
        result = append_image_recap("img-20260302-120000.jpg", caption="Sunset at Biopolis")
        assert result is True
        content = get_active_file().read_text()
        assert "![[img-20260302-120000.jpg]]" in content
        assert "Sunset at Biopolis" in content

    def test_image_without_caption(self):
        from recorder import append_image_recap, get_active_file
        append_image_recap("img-20260302-120000.jpg")
        content = get_active_file().read_text()
        # Should NOT have "None" in content
        assert "None" not in content

    def test_no_active_file(self):
        import recorder
        recorder._active_file = None
        from recorder import append_image_recap
        result = append_image_recap("img.jpg")
        assert result is False

    def test_image_in_recap_section(self):
        """Image should appear in Boyang's Recap section."""
        from recorder import append_image_recap, get_active_file
        append_image_recap("img-20260302-120000.jpg")
        content = get_active_file().read_text()
        # The image embed should come after "# Boyang's Recap"
        recap_idx = content.index("# Boyang's Recap")
        img_idx = content.index("![[img-20260302-120000.jpg]]")
        assert img_idx > recap_idx


class TestAppendFileRecap:

    @pytest.fixture(autouse=True)
    def setup(self, digest_dir):
        import recorder
        from recorder import create_digest
        recorder._active_file = None
        now = datetime.now(SGT)
        create_digest(
            coverage_from=now - timedelta(hours=1),
            coverage_to=now,
            session_summaries=[{"session": "Test", "messages": 1, "summary": "Test."}],
        )

    def test_basic_file_embed(self):
        from recorder import append_file_recap, get_active_file
        result = append_file_recap("file-20260302-120000-report.pdf")
        assert result is True
        content = get_active_file().read_text()
        assert "![[file-20260302-120000-report.pdf]]" in content
        assert "📎" in content

    def test_file_with_caption(self):
        from recorder import append_file_recap, get_active_file
        append_file_recap("file-20260302-120000-data.csv", caption="Q1 numbers")
        content = get_active_file().read_text()
        assert "![[file-20260302-120000-data.csv]]" in content
        assert "Q1 numbers" in content

    def test_no_active_file(self):
        import recorder
        recorder._active_file = None
        from recorder import append_file_recap
        assert append_file_recap("file.pdf") is False


# ============================================================
# Handler tests
# ============================================================

class TestHandlePhoto:

    @pytest.fixture(autouse=True)
    def setup(self, digest_dir):
        import recorder
        from recorder import create_digest
        from config import DIGEST_DIR
        recorder._active_file = None
        now = datetime.now(SGT)
        create_digest(
            coverage_from=now - timedelta(hours=1),
            coverage_to=now,
            session_summaries=[{"session": "Test", "messages": 1, "summary": "Test."}],
        )
        # Create attachments dir
        (DIGEST_DIR / "attachments").mkdir(parents=True, exist_ok=True)

    @pytest.mark.asyncio
    async def test_photo_downloads_and_records(self):
        import main as main_mod
        from config import ATTACHMENTS_DIR

        update = _make_update()

        # Mock photo (Telegram sends array of sizes, largest last)
        mock_photo_small = MagicMock()
        mock_photo_small.file_id = "small_id"
        mock_photo_large = MagicMock()
        mock_photo_large.file_id = "large_id"
        update.message.photo = [mock_photo_small, mock_photo_large]

        mock_file = AsyncMock()
        mock_file.download_to_drive = AsyncMock(
            side_effect=lambda path: Path(path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        )

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=mock_file)

        await main_mod.handle_photo(update, ctx)

        # Verify get_file was called with the LARGEST photo
        ctx.bot.get_file.assert_called_once_with("large_id")

        # Verify reply
        update.message.reply_text.assert_called_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "📷" in reply
        assert "✍️" in reply

        # Verify file saved
        imgs = list(ATTACHMENTS_DIR.glob("img-*.jpg"))
        assert len(imgs) >= 1

    @pytest.mark.asyncio
    async def test_photo_with_caption(self):
        import main as main_mod
        from recorder import get_active_file

        update = _make_update()
        update.message.caption = "Beautiful sunset"

        mock_photo = MagicMock()
        mock_photo.file_id = "photo_id"
        update.message.photo = [mock_photo]

        mock_file = AsyncMock()
        mock_file.download_to_drive = AsyncMock(
            side_effect=lambda path: Path(path).write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        )

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=mock_file)

        await main_mod.handle_photo(update, ctx)

        content = get_active_file().read_text()
        assert "Beautiful sunset" in content

    @pytest.mark.asyncio
    async def test_photo_rejected_unknown_user(self):
        import main as main_mod

        update = _make_update(user_id=999999)
        update.message.photo = [MagicMock()]
        ctx = MagicMock()

        await main_mod.handle_photo(update, ctx)
        update.message.reply_text.assert_not_called()


class TestHandleDocument:

    @pytest.fixture(autouse=True)
    def setup(self, digest_dir):
        import recorder
        from recorder import create_digest
        from config import DIGEST_DIR
        recorder._active_file = None
        now = datetime.now(SGT)
        create_digest(
            coverage_from=now - timedelta(hours=1),
            coverage_to=now,
            session_summaries=[{"session": "Test", "messages": 1, "summary": "Test."}],
        )
        (DIGEST_DIR / "attachments").mkdir(parents=True, exist_ok=True)

    @pytest.mark.asyncio
    async def test_document_with_filename(self):
        import main as main_mod
        from config import ATTACHMENTS_DIR

        update = _make_update()

        mock_doc = MagicMock()
        mock_doc.file_id = "doc_id"
        mock_doc.file_name = "report.pdf"
        mock_doc.mime_type = "application/pdf"
        update.message.document = mock_doc

        mock_file = AsyncMock()
        mock_file.download_to_drive = AsyncMock(
            side_effect=lambda path: Path(path).write_bytes(b"%PDF" + b"\x00" * 50)
        )

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=mock_file)

        await main_mod.handle_document(update, ctx)

        update.message.reply_text.assert_called_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "📎" in reply

        files = list(ATTACHMENTS_DIR.glob("file-*report.pdf"))
        assert len(files) >= 1

    @pytest.mark.asyncio
    async def test_document_without_filename(self):
        import main as main_mod
        from config import ATTACHMENTS_DIR

        update = _make_update()

        mock_doc = MagicMock()
        mock_doc.file_id = "doc_id"
        mock_doc.file_name = None
        mock_doc.mime_type = "application/pdf"
        update.message.document = mock_doc

        mock_file = AsyncMock()
        mock_file.download_to_drive = AsyncMock(
            side_effect=lambda path: Path(path).write_bytes(b"\x00" * 50)
        )

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.get_file = AsyncMock(return_value=mock_file)

        await main_mod.handle_document(update, ctx)

        files = list(ATTACHMENTS_DIR.glob("file-*.pdf"))
        assert len(files) >= 1

    @pytest.mark.asyncio
    async def test_document_rejected_unknown_user(self):
        import main as main_mod

        update = _make_update(user_id=999999)
        update.message.document = MagicMock()
        ctx = MagicMock()

        await main_mod.handle_document(update, ctx)
        update.message.reply_text.assert_not_called()
