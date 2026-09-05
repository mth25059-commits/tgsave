# tgsave — library compatibility shims
#
# Import this before anything touches a peer id.
#
# Pyrogram 2.0.106 (the last official release, 2023) hardcodes the pre-2024
# Telegram id ranges in pyrogram/utils.py:
#
#     MIN_CHANNEL_ID = -1002147483647     # channel ids capped at 2**31
#     MIN_CHAT_ID    = -2147483647
#
# Telegram has since handed out channel ids well past 2**31, so a link like
# t.me/c/3980676358/790 becomes peer -1003980676358, which falls outside that
# window and get_peer_type() raises "Peer id invalid" before a single request
# leaves the machine. Nothing is wrong with the account or the link.
#
# These are the current TDLib bounds. get_peer_type reads the module globals at
# call time, so widening them here is enough — no vendored library patch, and
# nothing to redo after a pip install.

from pyrogram import utils

MAX_CHAT_ID = 999999999999          # basic groups
MAX_CHANNEL_ID = 997852516352       # channels and supergroups

utils.MIN_CHAT_ID = -MAX_CHAT_ID
utils.MIN_CHANNEL_ID = utils.MAX_CHANNEL_ID - MAX_CHANNEL_ID
