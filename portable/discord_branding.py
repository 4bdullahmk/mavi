
def message_payload(text: str, title: str = "Mavi") -> dict:
    # Truncate title to 80 characters
    t = title[:80]
    
    # Handle empty text placeholder
    if not text:
        text = "No content provided."
        
    # Truncate description to 1800 UTF-16 code units
    # We iterate and count code units to ensure no unpaired surrogates are left
    desc = ""
    count = 0
    for char in text:
        if 0xD800 <= ord(char) <= 0xDFFF:
            continue
        # Calculate UTF-16 code units for this character
        # Basic multilingual plane (BMP) chars are 1 unit, others are 2
        if ord(char) <= 0xFFFF:
            units = 1
        else:
            units = 2
            
        if count + units > 1800:
            break
            
        desc += char
        count += units
        
    return {
        "embeds": [
            {
                "title": t,
                "description": desc,
                "color": 0x383838,
                "footer": {
                    "text": "Mavi · Your local workspace"
                }
            }
        ],
        "allowed_mentions": {
            "parse": []
        }
    }

def help_text() -> str:
    return (
        "Mavi · local workspace\n\n"
        "!mavi ask <request> — Ask a local model. You can attach UTF-8 text files.\n"
        "!mavi task <request> — Start a local multi-step task.\n"
        "!mavi image <prompt> — Generate an image.\n"
        "!mavi edit <instruction> — Edit 1–3 attached PNG or JPEG images.\n"
        "!mavi status — Check your task's progress.\n"
        "!mavi steer <instruction> — Guide an active chat, browser, or computer task.\n"
        "!mavi answer <reply> — Reply when Mavi is waiting for your input.\n"
        "!mavi stop — Stop your active task.\n"
        "!mavi help — Show this guide.\n\n"
        "Task, image, and edit commands require ‘Allow Discord to start local tasks’ in Mavi settings. "
        "Image generation and editing also require the optional local Qwen image runtime and a "
        "supported NVIDIA GPU.\n\n"
        "Models run on your computer, which must be awake. Discord needs internet, and channel "
        "members can see messages and files. Tasks may wait for your input or an approval; enter "
        "private credentials and complete approvals on your computer."
    )


def accepted_text() -> str:
    return "Accepted. Mavi is working locally; use !mavi status for updates."


def working_status_text(progress: str = "") -> str:
    detail = str(progress).strip()
    return "Working locally." + (" " + detail if detail else " Use !mavi status for updates.")


def waiting_status_text(question: str) -> str:
    detail = str(question).strip() or "Mavi needs your input to continue."
    return detail + "\nReply with !mavi answer <reply>. Complete approvals and enter credentials on your computer."


def disabled_error_text() -> str:
    return "Local task access is off. Enable ‘Allow Discord to start local tasks’ in Mavi settings."


def attachment_error_text() -> str:
    return "I couldn't use that attachment. Use UTF-8 text with !mavi ask, or PNG/JPEG images with !mavi edit."
