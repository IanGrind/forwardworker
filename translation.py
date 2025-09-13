import os
from config import Config

class Translation(object):
  START_TXT = """Hello there, {}.

A personal assistant for message forwarding.

Select 'Help' for a list of commands.
(o･ω･o)"""


  HELP_TXT = """<b>֎ Help Menu ֎</b>

Available commands:

● /start - Check if alive.
● /forward - Forward a range of messages.
● /tasks - View and manage active tasks.
● /settings - Open the configuration menu.
● /forwardelay - Set a custom forward delay (in seconds).

<b>Features:</b>
▸ Custom message ranges.
▸ Concurrent forwarding via multiple bots/userbots.
▸ Control over forward tag, captions, and buttons.
▸ Filtering of message types (text, photo, video, etc.).
▸ Real-time progress tracking with cancellation."""
  
  ABOUT_TXT = """<b>֎ About ֎</b>

● <b>Name:</b> {}
● <b>Language:</b> Python
● <b>Library:</b> Pyrogram
● <b>Developer:</b> <a href='https://t.me/partDevil'>partDevil</a>"""
  
  FROM_MSG = "<b>Step 2: Set Source & Range</b>\n\nForward the **last message** you want to forward from the source chat, or send its link.\n\nThe range will start from message ID 1 by default.\n\n/cancel - Abort mission."
  
  RANGE_SELECTION_TXT = """<b>֎ Message Range ֎</b>

The range is set from message <code>{start}</code> to <code>{end}</code>.

You can confirm to start, or edit the range below."""
  
  CAPTION_PROMPT = """<b><u>Set Custom Caption</u></b>

Send your custom caption text.

<b><u>Available Fillings:</u></b>
`{filename}` - The name of the file.
`{size}` - The size of the file.
`{caption}` - The original message caption.

/cancel - to abort."""

  BUTTON_PROMPT = """<b><u>Set Custom Button</u></b>

Send your button layout text.

<b><u>Format:</u></b>
`[Button Text](buttonurl://example.com)`

To have multiple buttons on one line, separate them with a pipe `|`:
`[Button 1](url1) | [Button 2](url2)`

For multiple lines, use a new line in the message.

/cancel - to abort."""

  CANCEL = "Process cancelled. (o˘◡˘o)"
  BOT_DETAILS = "<b>֎ Bot Details ֎</b>\n\n● <b>Name:</b> <code>{}</code>\n● <b>ID:</b> <code>{}</code>\n● <b>Username:</b> {}"
  USER_DETAILS = "<b>֎ Userbot Details ֎</b>\n\n● <b>Name:</b> <code>{}</code>\n● <b>ID:</b> <code>{}</code>\n● <b>Username:</b> {}"  
         
  TEXT = """<b>Status:</b> <code>{status}</code>

<b>Progress:</b> <code>{fetched} / {total}</code>
{progress_bar}
<b>Percentage:</b> <code>{percentage}%</code>

<b>Forwarded:</b> <code>{forwarded}</code>
<b>Skipped:</b> <code>{skipped}</code>
<b>Failed:</b> <code>{failed}</code>
<b>ETA:</b> <code>{eta}</code>
"""
  
  STATUS_ALERT = """Processed: {fetched}/{total} ({percentage}%)
Forwarded: {forwarded} | Failed: {failed}
Skipped: {skipped} | Status: {status}
ETA: {eta}"""

}
