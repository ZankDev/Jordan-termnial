"""Polymarket "momo" vertical — a separate, paper-money-only trading vertical that
ports the standalone PolyMarket/ Node.js bot's buzzer-momo strategy (5-minute crypto
Up-or-Down session momentum) into the terminal, plus a trainer, a live scanner
("momo radar"), a Field-page equivalent, a line-chart GP-chart equivalent, and a
real (paper-shadowed) wallet copytrader. See the plan at
C:\\Users\\yarde\\.claude\\plans\\so-i-want-u-breezy-otter.md for the full design.

Nothing in this package ever places a real order — every account here is a paper
account (see app/polymarket/ledger.py), same guarantee as the rest of NOSTRO today.
"""
