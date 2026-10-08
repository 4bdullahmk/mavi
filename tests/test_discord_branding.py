import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import discord_branding


class DiscordBrandingTests(unittest.TestCase):
    def test_help_matches_commands_and_real_capabilities(self):
        text = discord_branding.help_text()
        for command in ("ask", "task", "image", "edit", "status", "steer", "answer", "stop", "help"):
            self.assertIn("!mavi " + command, text)
        self.assertIn("UTF-8 text files", text)
        self.assertIn("1–3 attached PNG or JPEG", text)
        self.assertIn("optional local Qwen image runtime", text)
        self.assertIn("supported NVIDIA GPU", text)
        self.assertIn("channel members can see messages and files", text)
        self.assertIn("credentials", text)
        self.assertIn("computer", text)
        self.assertNotIn("private Discord", text)
        self.assertNotIn("secure and private", text)
        self.assertIn("chat, browser, or computer task", text)
        self.assertIn("when Mavi is waiting for your input", text)

    def test_status_and_error_copy_is_concise_and_truthful(self):
        self.assertIn("working locally", discord_branding.accepted_text().lower())
        self.assertIn("!mavi status", discord_branding.accepted_text())
        self.assertIn("Working locally", discord_branding.working_status_text("Writing the file"))
        waiting = discord_branding.waiting_status_text("Which format should I use?")
        self.assertIn("!mavi answer <reply>", waiting)
        self.assertIn("on your computer", waiting)
        self.assertIn("Allow Discord to start local tasks", discord_branding.disabled_error_text())
        attachment_error = discord_branding.attachment_error_text()
        self.assertIn("UTF-8 text", attachment_error)
        self.assertIn("PNG/JPEG", attachment_error)
        for text in (discord_branding.accepted_text(), discord_branding.working_status_text(),
                     waiting, discord_branding.disabled_error_text(), attachment_error):
            self.assertLessEqual(len(text), 300)

    def test_help_fits_branded_message_and_disables_mentions(self):
        payload = discord_branding.message_payload(discord_branding.help_text())
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        description = payload["embeds"][0]["description"]
        self.assertLessEqual(len(description.encode("utf-16-le")) // 2, 1800)
        self.assertIn("!mavi help", description)


if __name__ == "__main__":
    unittest.main()
