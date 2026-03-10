"""
BoardSense — curriculum.py
"Through the Rankings" training curriculum data and helpers.

8 stages from Foundation (0–400) to Mastery (1800–2000+).
Stages 1–4 are fully populated with curated positions and walkthroughs.
Stages 5–8 have 1–2 curated positions per module (supplemented by player games).
"""

import chess

# ---------------------------------------------------------------------------
# Curriculum data
# ---------------------------------------------------------------------------

CURRICULUM = {
    # ── Stage 1: Foundation (0–400) ───────────────────────────────────────────
    1: {
        "name": "Foundation",
        "rating_band": "0–400",
        "rating_range": (0, 400),
        "description": "Essential chess fundamentals",
        "modules": [
            {
                "id": "1.1",
                "title": "Back Rank Checkmate",
                "concept": "Back Rank Weakness",
                "positions": [
                    {
                        "fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1",
                        "best_move": "Ra8#",
                        "player_color": "white",
                    },
                    {
                        "fen": "r5k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1",
                        "best_move": "Re8+",
                        "player_color": "white",
                    },
                    {
                        "fen": "3r2k1/5ppp/8/8/8/5Q2/5PPP/6K1 b - - 0 1",
                        "best_move": "Rd1+",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "r5k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1",
                    "moves": ["Re8+", "Rxe8#"],
                    "annotations": [
                        "White has a rook on e1 and Black's king is boxed in behind its pawns on f7, g7, h7. The back rank is vulnerable.",
                        "Re8+ — the rook delivers check on the back rank. Black must respond to the check, but the only defender is the rook on a8.",
                        "After Black blocks or captures, the back rank remains weak. In many variations this leads to checkmate because the king has no escape squares through its own pawns.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.2",
                "title": "Two-Rook Ladder",
                "concept": "Rook On Seventh Rank",
                "positions": [
                    {
                        "fen": "8/8/8/8/8/4k3/8/RR4K1 w - - 0 1",
                        "best_move": "Ra3+",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/8/4k3/8/8/8/RR4K1 w - - 0 1",
                        "best_move": "Ra5+",
                        "player_color": "white",
                    },
                    {
                        "fen": "2K5/8/8/8/3k4/8/8/rr6 b - - 0 1",
                        "best_move": "Ra8+",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/8/8/8/4k3/8/RR4K1 w - - 0 1",
                    "moves": ["Ra3+", "Kf4", "Rb4+"],
                    "annotations": [
                        "White has two rooks against a lone king. The ladder mate works by cutting off the king rank by rank.",
                        "Ra3+ — the rook checks the king and forces it to move to a higher rank. The rook now controls the entire 3rd rank.",
                        "Kf4 — the king moves up. It has no choice but to advance toward the edge of the board.",
                        "Rb4+ — the second rook takes the next rank, creating a 'ladder'. Step by step, the king is pushed to the edge for checkmate.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.3",
                "title": "Queen + King Mate",
                "concept": "King Safety",
                "positions": [
                    {
                        "fen": "8/8/8/4k3/8/8/8/3Q1K2 w - - 0 1",
                        "best_move": "Qe2",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/8/8/5k2/8/8/3Q1K2 w - - 0 1",
                        "best_move": "Qd3",
                        "player_color": "white",
                    },
                    {
                        "fen": "k7/2Q5/K7/8/8/8/8/8 w - - 0 1",
                        "best_move": "Qb7#",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/8/3k4/8/8/8/4QK2 w - - 0 1",
                    "moves": ["Qe3"],
                    "annotations": [
                        "White has a queen and king vs lone king. The technique is to use the queen to restrict the king, then bring your own king up to deliver checkmate.",
                        "Qe3 — the queen centralises and creates a 'box'. From e3 it controls the e-file and the 3rd rank, dramatically reducing the king's available squares. Now bring your king closer step by step.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.4",
                "title": "Piece Values & Trading",
                "concept": "Piece Activity",
                "positions": [
                    {
                        "fen": "r1bqkbnr/pppppppp/2n5/8/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
                        "best_move": "d4",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "d3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2qkb1r/ppp2ppp/2np1n2/4p1B1/2B1P3/5N2/PPP2PPP/RN1QK2R w KQkq - 0 5",
                        "best_move": "Bxf6",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1b1kbnr/ppppqppp/2n5/4N3/4P3/8/PPPP1PPP/RNBQKB1R w KQkq - 3 4",
                    "moves": ["Nxc6", "dxc6"],
                    "annotations": [
                        "White's knight on e5 is attacked by Black's queen. Should White retreat or trade? Since the knight equals 3 points and captures Black's knight (also 3 points), this is an equal trade.",
                        "Nxc6 — White captures the knight. This is a fair exchange: knight for knight. Always count the piece values before trading.",
                        "dxc6 — Black recaptures. The trade is complete. Key lesson: trade when it's equal or favourable. Avoid giving up a piece worth more than what you capture.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.5",
                "title": "Center Control",
                "concept": "Centralization",
                "positions": [
                    {
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                        "best_move": "e4",
                        "player_color": "white",
                    },
                    {
                        "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                        "best_move": "e5",
                        "player_color": "black",
                    },
                    {
                        "fen": "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2",
                        "best_move": "exd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "moves": ["e4", "e5"],
                    "annotations": [
                        "The starting position. The four central squares (d4, d5, e4, e5) are the most important on the board. Pieces placed in or controlling the center have maximum influence.",
                        "e4 — White's pawn occupies a central square and opens diagonals for the queen and bishop. This is the most popular first move in chess.",
                        "e5 — Black fights for the center immediately. Both sides now have a pawn in the center. The battle for these key squares will shape the entire game.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.6",
                "title": "Develop Your Pieces",
                "concept": "Piece Activity",
                "positions": [
                    {
                        "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                        "best_move": "Nf3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
                        "best_move": "Bc4",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "O-O",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                    "moves": ["Nf3", "Nc6", "Bc4"],
                    "annotations": [
                        "After 1.e4 e5, White needs to develop pieces. Knights and bishops should come out before the queen. Each move should serve a purpose.",
                        "Nf3 — the knight develops to an active square, attacks the e5 pawn, and controls the center. Knights before bishops is a good rule of thumb.",
                        "Nc6 — Black develops a knight too, defending the e5 pawn. Both sides bring pieces into the game.",
                        "Bc4 — the bishop develops to an active diagonal, eyeing the f7 square. Now White is ready to castle on the next move. Development + king safety = a strong opening.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.7",
                "title": "King Safety Basics",
                "concept": "King Safety",
                "positions": [
                    {
                        "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "O-O",
                        "player_color": "white",
                    },
                    {
                        "fen": "rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
                        "best_move": "O-O",
                        "player_color": "black",
                    },
                    {
                        "fen": "r1bqk2r/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 5",
                        "best_move": "O-O",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                    "moves": ["O-O"],
                    "annotations": [
                        "White has developed the knight and bishop. The king is still in the center — vulnerable to tactics along the e-file and diagonals.",
                        "O-O — castling tucks the king safely behind the pawns and connects the rooks. This is one of the most important moves in any game. Castle early!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "1.8",
                "title": "Scholar's Mate Defense",
                "concept": "Back Rank Weakness",
                "positions": [
                    {
                        "fen": "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3",
                        "best_move": "g6",
                        "player_color": "black",
                    },
                    {
                        "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
                        "best_move": "Qf3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3",
                        "best_move": "Nf6",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3",
                    "moves": ["g6", "Qf3", "Nf6"],
                    "annotations": [
                        "White is threatening Scholar's Mate with Qxf7#. The queen on h5 and bishop on c4 both target f7. Black must defend!",
                        "g6 — this pawn move attacks the queen and forces it to retreat. The f7 square is now defended, and White's early queen sortie has wasted time.",
                        "Qf3 — the queen retreats but stays on the f7 diagonal. White may try again, but Black now has time to develop.",
                        "Nf6 — the knight develops with tempo, attacking the queen again. Black is now ahead in development because White moved the queen too early. Lesson: don't bring your queen out early!",
                    ],
                    "player_color": "black",
                },
            },
        ],
    },

    # ── Stage 2: Pattern Recognition (400–800) ───────────────────────────────
    2: {
        "name": "Pattern Recognition",
        "rating_band": "400–800",
        "rating_range": (400, 800),
        "description": "Recognise and execute basic tactical patterns",
        "modules": [
            {
                "id": "2.1",
                "title": "Knight Fork",
                "concept": "Fork",
                "positions": [
                    {
                        "fen": "r1bqkb1r/pppppppp/5n2/8/3nP3/3B1N2/PPP2PPP/RNBQK2R b KQkq - 0 4",
                        "best_move": "Nxe2",
                        "player_color": "black",
                    },
                    {
                        "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "Ng5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2qk2r/ppp2ppp/2npbn2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w kq - 0 6",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r3k3/ppp2ppp/8/3N4/4P3/8/PPP2PPP/R3K2R w KQq - 0 1",
                    "moves": ["Nc7+"],
                    "annotations": [
                        "White's knight sits on d5. Look at which squares it can jump to — and what pieces sit on those squares. From c7, the knight would attack both e8 and a8!",
                        "Nc7+ — a knight fork! The knight checks the king on e8 while simultaneously attacking the rook on a8. After the king moves, White captures the rook. The knight's L-shaped jump creates devastating double attacks.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.2",
                "title": "Queen & Pawn Forks",
                "concept": "Fork",
                "positions": [
                    {
                        "fen": "rnb1kbnr/pppp1ppp/8/4p3/4PP1q/8/PPPP2PP/RNBQKBNR w KQkq - 1 3",
                        "best_move": "g3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqkbnr/1ppp1ppp/p1n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 4",
                        "best_move": "Qf7#",
                        "player_color": "white",
                    },
                    {
                        "fen": "rnbqkb1r/ppp1pppp/5n2/3P4/8/8/PPPP1PPP/RNBQKBNR w KQkq - 1 3",
                        "best_move": "c4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1b1k1nr/pppp1ppp/2n5/2b1p1q1/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 0 5",
                    "moves": ["d4"],
                    "annotations": [
                        "Black's bishop on c5 and the e5 pawn are both on light squares along a diagonal. White can exploit this alignment.",
                        "d4 — the pawn attacks both the bishop on c5 and opens the center. Black cannot save both the bishop and the pawn. This is a pawn fork — the cheapest piece creating a double attack.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.3",
                "title": "Pin",
                "concept": "Pin",
                "positions": [
                    {
                        "fen": "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/2N5/PPPP1PPP/R1BQKBNR b KQkq - 2 2",
                        "best_move": "Bb4",
                        "player_color": "black",
                    },
                    {
                        "fen": "rn1qkbnr/ppp1pppp/8/3p1b2/3PP3/5N2/PPP2PPP/RNBQKB1R w KQkq - 1 3",
                        "best_move": "Bd3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqk2r/ppppnppp/2n5/1Bb1p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "Bxc6",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "rn1qkb1r/ppp1pppp/5n2/3p4/3PP1b1/5N2/PPP2PPP/RNBQKB1R w KQkq - 2 3",
                    "moves": ["Be2"],
                    "annotations": [
                        "Black's bishop on g4 pins White's knight on f3 to the queen on d1. The knight cannot move without exposing the queen. This is an absolute pin when the piece behind is the king, and a relative pin when it's another piece.",
                        "Be2 — White breaks the pin by developing the bishop to e2, blocking the pin line. Now the knight on f3 is free to move again. Always look for ways to break pins: block, move the valuable piece, or counter-attack.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.4",
                "title": "Skewer",
                "concept": "Pin",
                "positions": [
                    {
                        "fen": "r5k1/8/8/8/8/8/8/4R1K1 w - - 0 1",
                        "best_move": "Re8+",
                        "player_color": "white",
                    },
                    {
                        "fen": "6k1/8/6K1/8/8/8/8/3R4 w - - 0 1",
                        "best_move": "Rd8+",
                        "player_color": "white",
                    },
                    {
                        "fen": "2kr4/8/8/8/8/8/8/2K1R3 w - - 0 1",
                        "best_move": "Re8+",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r5k1/8/8/8/8/8/8/4R1K1 w - - 0 1",
                    "moves": ["Re8+"],
                    "annotations": [
                        "White's rook is on e1. Black's king is on g8, and Black's rook is on a8. They're all on the same rank (the 8th). A skewer attacks the more valuable piece first, forcing it to move and exposing the piece behind it.",
                        "Re8+ — the rook invades the 8th rank with check. After the king steps aside, White plays Rxa8, winning the rook. A skewer is like a reverse pin: the valuable piece (king) is in front, and you capture what's behind it.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.5",
                "title": "Back Rank Patterns",
                "concept": "Back Rank Weakness",
                "positions": [
                    {
                        "fen": "2r3k1/5ppp/8/8/8/8/5PPP/1R4K1 w - - 0 1",
                        "best_move": "Rb8+",
                        "player_color": "white",
                    },
                    {
                        "fen": "5rk1/5ppp/8/8/8/8/2Q2PPP/6K1 w - - 0 1",
                        "best_move": "Qc8",
                        "player_color": "white",
                    },
                    {
                        "fen": "6k1/5ppp/8/8/8/7P/r4PP1/6K1 b - - 0 1",
                        "best_move": "Ra1#",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "5rk1/5ppp/8/8/8/8/2Q2PPP/6K1 w - - 0 1",
                    "moves": ["Qc8"],
                    "annotations": [
                        "Black's king is behind its pawns with only one rook defending the back rank. White's queen can exploit this weakness.",
                        "Qc8 — the queen invades the 8th rank, pinning and winning Black's rook. If the rook moves, Qf8# is checkmate. Back rank threats work even with queens!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.6",
                "title": "Trapped Piece",
                "concept": "Trapped Piece",
                "positions": [
                    {
                        "fen": "rnbqk1nr/pppp1ppp/8/4p3/1b2P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 2 3",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                    {
                        "fen": "rn1qkbnr/ppp2ppp/4p3/3pPb2/3P4/2N5/PPP2PPP/R1BQKBNR w KQkq - 0 4",
                        "best_move": "g4",
                        "player_color": "white",
                    },
                    {
                        "fen": "rnbqkbnr/pppp1p1p/6p1/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 0 3",
                        "best_move": "Qxe5+",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "rn1qkbnr/ppp2ppp/4p3/3pPb2/3P4/2N5/PPP2PPP/R1BQKBNR w KQkq - 0 4",
                    "moves": ["g4", "Bg6"],
                    "annotations": [
                        "Black's bishop has ventured to f5, outside its pawn chain. The question is: can it escape if attacked?",
                        "g4 — White attacks the bishop. Where can it go? The bishop is short of safe squares. It must retreat toward the kingside.",
                        "Bg6 — the only square, but now h4-h5 will trap the bishop completely. The lesson: before developing a piece to an advanced square, make sure it has a safe retreat.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.7",
                "title": "Discovered Attack",
                "concept": "Discovered Attack",
                "positions": [
                    {
                        "fen": "r1bqk2r/pppp1ppp/2n5/2b1p3/2BnP3/5N2/PPPP1PPP/RNBQ1RK1 w kq - 5 5",
                        "best_move": "Nxd4",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1b1kbnr/pppp1ppp/2n5/4p3/2B1P1q1/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "Bxf7+",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1b1kbnr/ppppqppp/2n5/4p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 2 4",
                    "moves": ["Bg5"],
                    "annotations": [
                        "White can pin the f6 square and set up a potential discovered attack. When a piece moves and uncovers an attack from another piece behind it, that's a discovered attack.",
                        "Bg5 — the bishop pins the potential f6 knight to the queen. In many positions like this, if a piece on the d-file moves, it reveals an attack from the rook or queen behind. Always look for pieces lined up on files and diagonals!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "2.8",
                "title": "Simple Combinations",
                "concept": "Fork",
                "positions": [
                    {
                        "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "Ng5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2qkbnr/ppp2ppp/2np4/4p1B1/2B1P1b1/5N2/PPP2PPP/RN1QK2R w KQkq - 2 5",
                        "best_move": "Bxf7+",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqk2r/pppp1Npp/2n2n2/2b1p3/2B1P3/8/PPPP1PPP/RNBQK2R b KQkq - 0 5",
                        "best_move": "Qe7",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                    "moves": ["Ng5"],
                    "annotations": [
                        "The Italian Game position. White's bishop on c4 already eyes f7, the weakest square in Black's position (defended only by the king).",
                        "Ng5 — the knight attacks f7 alongside the bishop. This is a simple two-piece combination. Even if Black can defend, this double attack on f7 creates real problems. Combinations start by noticing which squares are under-defended.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 3: Building Skills (800–1000) ───────────────────────────────────
    3: {
        "name": "Building Skills",
        "rating_band": "800–1000",
        "rating_range": (800, 1000),
        "description": "Deepen tactical understanding and learn key endgame ideas",
        "modules": [
            {
                "id": "3.1",
                "title": "Removing the Defender",
                "concept": "Deflection",
                "positions": [
                    {
                        "fen": "r1b2rk1/pp3ppp/2p1pn2/q2n4/2BP4/2N2N2/PP3PPP/R1BQ1RK1 w - - 0 9",
                        "best_move": "Bxd5",
                        "player_color": "white",
                    },
                    {
                        "fen": "2rq1rk1/pp1bppbp/2np1np1/8/2BNP3/2N1BP2/PPPQ2PP/R4RK1 w - - 0 10",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r4rk1/pppb1ppp/2n1pn2/3q4/3P4/2NBPN2/PPP2PPP/R2Q1RK1 w - - 0 8",
                        "best_move": "Nxd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1b1r1k1/pp3ppp/2p2n2/3n4/2BP4/2N5/PP3PPP/R1BQ1RK1 w - - 0 10",
                    "moves": ["Bxd5", "cxd5"],
                    "annotations": [
                        "Black's knight on d5 is the key defender — it controls several important squares and blocks the d-file. If White can remove it, the position opens up.",
                        "Bxd5 — White captures the defensive knight. Even though Black recaptures, removing this key piece weakens Black's position significantly.",
                        "cxd5 — Black recaptures, but now the pawn structure is changed. The d5 pawn is isolated and the c-file is open. Removing the defender often transforms the entire position.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.2",
                "title": "Overloading",
                "concept": "Overloading",
                "positions": [
                    {
                        "fen": "3r2k1/pp3ppp/2p5/4q3/8/2P2Q2/PP3PPP/3R2K1 w - - 0 1",
                        "best_move": "Rd8+",
                        "player_color": "white",
                    },
                    {
                        "fen": "r4rk1/1bq2ppp/p2ppn2/1p6/3QP3/1BN2P2/PPP3PP/R4RK1 w - - 0 12",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "3r2k1/pp3ppp/2p5/4q3/8/2P2Q2/PP3PPP/3R2K1 w - - 0 1",
                    "moves": ["Rd8+"],
                    "annotations": [
                        "Black's queen on e5 is the only piece defending the rook on d8. If White attacks the rook, the queen must choose between two duties — she's overloaded.",
                        "Rd8+ — White's rook checks via the back rank. The queen must abandon its post to deal with the check, and White wins material. An overloaded piece is trying to do two jobs at once.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.3",
                "title": "Double Check",
                "concept": "Double Check",
                "positions": [
                    {
                        "fen": "rnb1kbnr/pppp1ppp/8/4p3/2B1P1q1/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "Bxf7+",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqk2r/pppp1Npp/2n2n2/2b1p3/2B1P3/8/PPPP1PPP/RNBQK2R b KQkq - 0 5",
                        "best_move": "Ke7",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "4k3/5N2/8/7Q/8/8/8/6K1 w - - 0 1",
                    "moves": ["Nd6+"],
                    "annotations": [
                        "White has a queen on h5 and a knight on f7. The knight blocks the queen's diagonal (h5-g6-f7-e8) toward the king. What if the knight moves to a square that ALSO gives check?",
                        "Nd6+ — double check! The knight checks from d6 AND the queen checks from h5 (the knight unmasked the diagonal). In a double check, the only legal response is to move the king — you cannot block or capture two checking pieces simultaneously.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.4",
                "title": "Passed Pawn Basics",
                "concept": "Passed Pawn",
                "positions": [
                    {
                        "fen": "8/5kpp/8/3P4/8/8/5PPP/6K1 w - - 0 1",
                        "best_move": "d6",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/5k2/8/1p6/8/8/5K2 b - - 0 1",
                        "best_move": "b3",
                        "player_color": "black",
                    },
                    {
                        "fen": "8/2k5/8/8/P7/8/6K1/8 w - - 0 1",
                        "best_move": "a5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/5kpp/8/3P4/8/8/5PPP/6K1 w - - 0 1",
                    "moves": ["d6", "Ke6"],
                    "annotations": [
                        "White has a passed pawn on d5 — no enemy pawn can block or capture it on d, c, or e files. Passed pawns are powerful because they threaten to promote.",
                        "d6 — advance the passed pawn! Every square it gains brings it closer to becoming a queen. The opponent must use pieces to stop it.",
                        "Ke6 — Black rushes the king to blockade the pawn. This is the correct defense: place a piece in front of the passed pawn. But Black's king is now tied down, giving White a free hand elsewhere.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.5",
                "title": "Rook on Open File",
                "concept": "Rook On Open File",
                "positions": [
                    {
                        "fen": "r2r2k1/pp3ppp/2p2n2/4p3/4P3/2N2N2/PPP2PPP/3RR1K1 w - - 0 10",
                        "best_move": "Rd7",
                        "player_color": "white",
                    },
                    {
                        "fen": "r3r1k1/ppp2ppp/2n2n2/3p4/3P4/2N2N2/PPP2PPP/R3R1K1 w - - 0 8",
                        "best_move": "Re5",
                        "player_color": "white",
                    },
                    {
                        "fen": "2r2rk1/pp3ppp/2p2n2/3p4/3P4/2P2N2/PP3PPP/2R2RK1 w - - 0 10",
                        "best_move": "Rc2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r3rbk1/ppp2ppp/2n2n2/3p4/3P4/2N2N2/PPP2PPP/R3R1K1 w - - 0 8",
                    "moves": ["Re5"],
                    "annotations": [
                        "The e-file is open — no pawns on it for either side. White should seize control of this file with a rook.",
                        "Re5 — the rook occupies the open file and reaches the 5th rank. From here it controls both vertical and horizontal lines. Open files are highways for rooks — always look to place your rooks on them.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.6",
                "title": "Rook on the Seventh",
                "concept": "Rook On Seventh Rank",
                "positions": [
                    {
                        "fen": "6k1/1pp2ppp/p7/8/8/P7/1PP2PPP/4R1K1 w - - 0 1",
                        "best_move": "Re7",
                        "player_color": "white",
                    },
                    {
                        "fen": "3r2k1/ppp2ppp/8/8/8/8/PPP2PPP/3R2K1 b - - 0 1",
                        "best_move": "Rd2",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "6k1/1pp2ppp/p7/8/8/P7/1PP2PPP/4R1K1 w - - 0 1",
                    "moves": ["Re7"],
                    "annotations": [
                        "White's rook can penetrate to the 7th rank, where Black's pawns live. A rook on the 7th is one of the most powerful pieces on the board.",
                        "Re7 — the rook attacks the b7 and c7 pawns simultaneously and restricts Black's king. The saying 'a rook on the seventh is worth a pawn' is often an understatement.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.7",
                "title": "Piece Activity",
                "concept": "Piece Activity",
                "positions": [
                    {
                        "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                        "best_move": "d3",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2qkbnr/ppp1pppp/2n5/3p4/3PP1b1/5N2/PPP2PPP/RNBQKB1R w KQkq - 2 3",
                        "best_move": "c4",
                        "player_color": "white",
                    },
                    {
                        "fen": "rnbqk2r/pppp1ppp/5n2/2b1p3/4P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 4 4",
                        "best_move": "Bc4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqkb1r/pppp1ppp/2n2n2/4p3/4P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 4 4",
                    "moves": ["Bc4", "Bc5", "d3"],
                    "annotations": [
                        "White has developed both knights but the bishops are still on their starting squares. Every undeveloped piece is a piece not fighting.",
                        "Bc4 — the bishop goes to its most active diagonal, targeting f7 and controlling the center. Active pieces = winning chances.",
                        "Bc5 — Black develops similarly. The bishop targets the a7-g1 diagonal and the f2 square.",
                        "d3 — opens the diagonal for the dark-squared bishop. Now White can complete development and castle. Every piece should have a job to do.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "3.8",
                "title": "Basic Endgame Principles",
                "concept": "Opposition",
                "positions": [
                    {
                        "fen": "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1",
                        "best_move": "Ke4",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/8/3pk3/8/3PK3/8/8 w - - 0 1",
                        "best_move": "Kd2",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/6k1/8/4PK2/8/8/8/8 w - - 0 1",
                        "best_move": "Ke6",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1",
                    "moves": ["Ke4"],
                    "annotations": [
                        "King and pawn vs king — the most fundamental endgame. The key concept is opposition: when kings face each other with one square between them, the side NOT to move has the opposition (an advantage).",
                        "Ke4 — White advances the king AHEAD of the pawn. This is critical: the king must lead the pawn, not follow it. With the king on e4, White controls the key squares d5, e5, and f5 that will help escort the pawn.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 4: Intermediate (1000–1200) ─────────────────────────────────────
    4: {
        "name": "Intermediate",
        "rating_band": "1000–1200",
        "rating_range": (1000, 1200),
        "description": "Pawn structures, positional ideas, and essential endgames",
        "modules": [
            {
                "id": "4.1",
                "title": "Pawn Structure Fundamentals",
                "concept": "Isolated Pawn",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/2p5/2P1P3/2NP1N2/PP2BPPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "d4",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqr1k1/ppp2ppp/2n2n2/3P4/3P4/2N5/PP2PPPP/R1BQKB1R w KQ - 0 8",
                        "best_move": "Bg5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2q1rk1/ppp1bppp/2n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "Ne5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["Ne5", "Nxe5"],
                    "annotations": [
                        "White has an isolated d4 pawn — no pawns on c or e file to support it. But it controls key central squares and gives White active piece play.",
                        "Ne5 — the knight occupies a powerful outpost, supported by the d4 pawn. The isolated pawn's strength is the active piece play it provides.",
                        "Nxe5 — if Black trades, White recaptures and maintains the strong central presence. The tradeoff: an isolated pawn is weak in endgames but powerful in middlegames.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.2",
                "title": "Good vs Bad Bishop",
                "concept": "Bad Bishop",
                "positions": [
                    {
                        "fen": "r2q1rk1/pp2bppp/2n1pn2/2pp4/3P1B2/2PBPN2/PP3PPP/R2Q1RK1 w - - 0 8",
                        "best_move": "Bb5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r4rk1/pp2bppp/4pn2/2pp4/3P1B2/4PN2/PP3PPP/R3R1K1 w - - 0 10",
                        "best_move": "dxc5",
                        "player_color": "white",
                    },
                    {
                        "fen": "2r2rk1/pp2bppp/4pn2/2Pp4/5B2/4PN2/PP3PPP/2R2RK1 w - - 0 12",
                        "best_move": "Nd4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pppbbppp/4pn2/3p4/3P4/3BPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["Bd2"],
                    "annotations": [
                        "Black's bishop on d7 is hemmed in by pawns on e6, d5, and c7 — all on light squares. This is a 'bad bishop' because its own pawns block its diagonals.",
                        "Bd2 — White's dark-squared bishop is free and active. The key insight: put your pawns on the OPPOSITE color from your bishop so they don't obstruct it. A bishop restricted by its own pawns is a liability.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.3",
                "title": "Outpost Play",
                "concept": "Outpost",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/2p5/2P1P3/2NP1NP1/PP3PBP/R1BQ1RK1 w - - 0 7",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r2qr1k1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                        "best_move": "Ne5",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2PNP3/2N3P1/PP2PPBP/R1BQ1RK1 w - - 0 7",
                        "best_move": "Nc6",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2qr1k1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                    "moves": ["Ne5"],
                    "annotations": [
                        "The e5 square cannot be attacked by any Black pawn (d and f pawns are gone or blocked). This makes it a permanent outpost — a square where your piece can sit safely.",
                        "Ne5 — the knight lands on the outpost. It cannot be driven away by pawns and controls key squares d7, f7, d3, f3, c6, g6. An outposted knight in the center is worth nearly as much as a rook.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.4",
                "title": "Deflection",
                "concept": "Deflection",
                "positions": [
                    {
                        "fen": "2rq1rk1/pp3ppp/2n1b3/3Np3/2B5/8/PPP2PPP/R2Q1RK1 w - - 0 12",
                        "best_move": "Nf6+",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1b2rk1/pp3ppp/2p1pn2/q7/2BP4/2N2Q2/PP3PPP/R1B2RK1 w - - 0 10",
                        "best_move": "Nd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "2rq1rk1/pp3ppp/2n1b3/3Np3/2B5/8/PPP2PPP/R2Q1RK1 w - - 0 12",
                    "moves": ["Nf6+"],
                    "annotations": [
                        "White's knight can check on f6, but what does this achieve beyond just a check? Look at what the g7 pawn is defending.",
                        "Nf6+ — the knight checks, forcing the king to move (gxf6 weakens the king). This deflects the king away from guarding key squares. Deflection forces a piece away from its defensive duty.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.5",
                "title": "Decoy",
                "concept": "Decoy",
                "positions": [
                    {
                        "fen": "r1b2rk1/pp2Rppp/2p5/q7/2B5/2N5/PPP2PPP/R2Q2K1 w - - 0 12",
                        "best_move": "Re8",
                        "player_color": "white",
                    },
                    {
                        "fen": "3r2k1/ppp2ppp/8/3q4/3P4/5Q2/PPP3PP/5RK1 w - - 0 1",
                        "best_move": "Qf6",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1b2rk1/pp2Rppp/2p5/q7/2B5/2N5/PPP2PPP/R2Q2K1 w - - 0 12",
                    "moves": ["Re8"],
                    "annotations": [
                        "White's rook invades the 8th rank. Black's rook on f8 must deal with this threat, but moving it opens the king.",
                        "Re8 — this is a decoy: the rook lures Black's pieces to unfavourable squares. If Rxe8, White plays a devastating discovered check. The decoy sacrifice forces the opponent into a worse position by making them capture.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.6",
                "title": "Lucena Position",
                "concept": "Lucena Position",
                "positions": [
                    {
                        "fen": "1K1R4/1P6/8/8/8/k7/8/3r4 w - - 0 1",
                        "best_move": "Rd4",
                        "player_color": "white",
                    },
                    {
                        "fen": "3K4/3P4/8/8/3R4/8/8/k2r4 w - - 0 1",
                        "best_move": "Ke7",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "1K1R4/1P6/8/8/8/k7/8/3r4 w - - 0 1",
                    "moves": ["Rd4", "Rd2", "Kc7"],
                    "annotations": [
                        "The Lucena position: White's king is on b8 next to a pawn on b7, and the rook is on d8. Black's rook prevents the king from coming out. This is a winning technique every player must know.",
                        "Rd4 — the 'bridge' begins! The rook moves to the 4th rank. The idea is to use the rook as a shield (bridge) for the king to cross over.",
                        "Rd2 — Black tries to set up checking distance. The rook needs to stay active.",
                        "Kc7 — the king steps out. Now when Black gives checks, White's rook on d4 will block the check by going to d5, d6, etc. This 'building a bridge' technique wins every Lucena position.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.7",
                "title": "Philidor Position",
                "concept": "Philidor Position",
                "positions": [
                    {
                        "fen": "8/3k4/8/3KP3/8/8/8/r4R2 b - - 0 1",
                        "best_move": "Ra6",
                        "player_color": "black",
                    },
                    {
                        "fen": "4K3/4P3/8/8/4k3/8/8/r4R2 b - - 0 1",
                        "best_move": "Ra8+",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "8/3k4/8/3KP3/8/8/8/r4R2 b - - 0 1",
                    "moves": ["Ra6"],
                    "annotations": [
                        "The Philidor position is the key defensive technique in rook endgames. White has king + pawn + rook vs Black's king + rook. Black needs to draw.",
                        "Ra6 — the rook goes to the 6th rank (the 3rd rank from Black's perspective). This is the Philidor defense: keep your rook on the 6th rank to block the enemy king from advancing. If the pawn advances to e6, switch to checking from behind.",
                    ],
                    "player_color": "black",
                },
            },
            {
                "id": "4.8",
                "title": "Opposition",
                "concept": "Opposition",
                "positions": [
                    {
                        "fen": "8/8/4k3/8/4P3/5K2/8/8 w - - 0 1",
                        "best_move": "Kf4",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1",
                        "best_move": "Kf3",
                        "player_color": "white",
                    },
                    {
                        "fen": "8/8/3k4/8/8/3KP3/8/8 w - - 0 1",
                        "best_move": "Kd4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/8/4k3/8/4K3/4P3/8 w - - 0 1",
                    "moves": ["Kf3", "Kd4"],
                    "annotations": [
                        "King and pawn vs king. The key question: can the stronger side's king get in front of the pawn? Opposition means kings face each other with one square between them.",
                        "Kf3 — White does NOT push the pawn yet. Instead, the king advances first. The golden rule: the king must lead the pawn in these endgames.",
                        "Kd4 — after Black moves aside, White's king can advance further. By gaining the opposition (putting kings face-to-face and passing the move to the opponent), White's king gradually outflanks Black's king.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "4.9",
                "title": "Zwischenzug",
                "concept": "Zwischenzug",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2nppp/2p1p3/3pP3/3P4/2PB4/PP3PPP/R1BQ1RK1 w - - 0 9",
                        "best_move": "Bxh7+",
                        "player_color": "white",
                    },
                    {
                        "fen": "r1bqr1k1/ppp2ppp/2n2n2/3Np3/2B5/8/PPP2PPP/R1BQ1RK1 w - - 0 8",
                        "best_move": "Nxf6+",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqr1k1/ppp2ppp/2n2n2/3Np3/2B5/8/PPP2PPP/R1BQ1RK1 w - - 0 8",
                    "moves": ["Nxf6+"],
                    "annotations": [
                        "Instead of recapturing an expected piece, White throws in an unexpected check first. This is a zwischenzug — an 'in-between move'.",
                        "Nxf6+ — check! Before doing the expected thing, White inserts this forcing move. The opponent MUST deal with the check, and then White proceeds with a better position. Always ask: 'Is there a check, capture, or threat I can insert first?'",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 5: Positional (1200–1400) ───────────────────────────────────────
    5: {
        "name": "Positional",
        "rating_band": "1200–1400",
        "rating_range": (1200, 1400),
        "description": "Positional understanding and strategic play",
        "modules": [
            {
                "id": "5.1",
                "title": "Space Advantage",
                "concept": "Space Advantage",
                "positions": [
                    {
                        "fen": "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
                        "best_move": "c3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqkbnr/ppp2ppp/2np4/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
                    "moves": ["c3", "Nf6", "d4"],
                    "annotations": [
                        "White's pawns on e4 and the upcoming d4 will control more territory. Space advantage means your pieces have more room to maneuver.",
                        "c3 — preparing d4. White will establish a broad pawn center controlling d4, e4, and more. This restricts Black's pieces.",
                        "Nf6 — Black develops, but White is about to seize the center with the next move.",
                        "d4 — now White controls a huge swath of the center. With more space, White's pieces can reposition freely while Black's are cramped behind the pawn chain.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.2",
                "title": "Weak Squares",
                "concept": "Outpost",
                "positions": [
                    {
                        "fen": "r2q1rk1/pp1bppbp/2np1np1/2p5/2P1P3/2NP1NP1/PP3PBP/R1BQ1RK1 w - - 0 7",
                        "best_move": "d4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pp2ppbp/2np1np1/2p5/2P1P3/2NP1NP1/PP3PBP/R1BQ1RK1 w - - 0 8",
                    "moves": ["d4"],
                    "annotations": [
                        "After Black plays ...g6, the f6 and h6 squares are weakened because the g-pawn no longer controls them. These 'holes' become targets.",
                        "d4 — challenging the center while eyeing the weakened dark squares. A weak square is one that cannot be defended by a pawn. Place your pieces on your opponent's weak squares!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.3",
                "title": "Minority Attack",
                "concept": "Minority Attack",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp1n1ppp/2pbpn2/8/2PP4/2N2N2/PPQ1BPPP/R1B2RK1 w - - 0 8",
                        "best_move": "b4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/pp1n1ppp/2pbpn2/8/2PP4/2N2N2/PPQ1BPPP/R1B2RK1 w - - 0 8",
                    "moves": ["b4"],
                    "annotations": [
                        "White has 2 pawns on the queenside (a2, b2) vs Black's 3 (a7, b7, c6). By advancing the minority, White aims to create weaknesses in Black's pawn structure.",
                        "b4 — the minority attack begins. The idea: push b4-b5 to force ...cxb5, leaving Black with an isolated pawn on d5 or a backward pawn on c6. Fewer pawns can create weaknesses in a larger pawn chain.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.4",
                "title": "Prophylaxis Intro",
                "concept": "Prophylaxis",
                "positions": [
                    {
                        "fen": "r2q1rk1/1pp2ppp/p1np1n2/2b1p1B1/2B1P1b1/2NP1N2/PPP2PPP/R2Q1RK1 w - - 0 7",
                        "best_move": "h3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/1pp2ppp/p1np1n2/2b1p1B1/2B1P1b1/2NP1N2/PPP2PPP/R2Q1RK1 w - - 0 7",
                    "moves": ["h3"],
                    "annotations": [
                        "Before executing your own plan, ask: 'What is my opponent threatening?' Black's bishop on g4 pins the knight and creates pressure.",
                        "h3 — a prophylactic move. It asks the bishop 'where are you going?' Before pursuing your own strategy, neutralize your opponent's ideas. Prophylaxis is thinking for both sides.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.5",
                "title": "Open vs Closed Positions",
                "concept": "Bishop Pair",
                "positions": [
                    {
                        "fen": "r1bqkb1r/ppp2ppp/2np1n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 4",
                        "best_move": "d4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqkb1r/ppp2ppp/2np1n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 4 4",
                    "moves": ["d4"],
                    "annotations": [
                        "Bishops love open positions with long diagonals. Knights prefer closed positions with fixed pawn structures where they can hop over obstacles.",
                        "d4 — opening the position. With bishops still on the board, White wants open lines. In closed positions, trade bishops for knights. In open positions, keep your bishops. Match your pieces to the pawn structure.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.6",
                "title": "Positional Sacrifices",
                "concept": "Initiative",
                "positions": [
                    {
                        "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 4",
                        "best_move": "Bxf7+",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 4",
                    "moves": ["Bxf7+"],
                    "annotations": [
                        "Not every sacrifice needs to win material back immediately. Sometimes you sacrifice for long-term positional compensation — an exposed king, development lead, or initiative.",
                        "Bxf7+ — the classic bishop sacrifice on f7. White gives up a bishop but exposes Black's king, gains tempo, and seizes the initiative. Material is only one factor; activity and king safety matter too.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.7",
                "title": "Pawn Breaks",
                "concept": "Passed Pawn",
                "positions": [
                    {
                        "fen": "r2q1rk1/pp2bppp/2n1pn2/2ppP3/3P4/2PBB3/PP2NPPP/R2Q1RK1 w - - 0 9",
                        "best_move": "f4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pp2bppp/2n1pn2/2ppP3/3P4/2PBB3/PP2NPPP/R2Q1RK1 w - - 0 9",
                    "moves": ["f4"],
                    "annotations": [
                        "When the center is locked, look for pawn breaks to open lines. A pawn break is a pawn advance that challenges the opponent's pawn chain.",
                        "f4 — this break challenges Black's center and opens the f-file for White's rook. Pawn breaks create dynamic play in otherwise static positions. Always have a pawn break plan!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "5.8",
                "title": "Bishop Endgames",
                "concept": "Bishop vs Knight",
                "positions": [
                    {
                        "fen": "8/5kpp/4p3/3pP3/2pP4/2P2B2/6PP/6K1 w - - 0 1",
                        "best_move": "g4",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/5kpp/4p3/3pP3/2pP4/2P2B2/6PP/6K1 w - - 0 1",
                    "moves": ["g4"],
                    "annotations": [
                        "In bishop endgames, pawns on both sides of the board favor the bishop (long range). Fixed pawn structures determine whether a bishop is good or bad.",
                        "g4 — creating a passed pawn possibility on the kingside while the bishop covers the queenside. Bishops excel when play spans both flanks. Centralize your king, create a passed pawn, and use the bishop's long-range power.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 6: Strategic (1400–1600) ────────────────────────────────────────
    6: {
        "name": "Strategic",
        "rating_band": "1400–1600",
        "rating_range": (1400, 1600),
        "description": "Deep strategic planning and technique",
        "modules": [
            {
                "id": "6.1",
                "title": "Prophylaxis as Habit",
                "concept": "Prophylaxis",
                "positions": [
                    {
                        "fen": "r2q1rk1/ppp1bppp/2n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "a3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/ppp1bppp/2n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["a3"],
                    "annotations": [
                        "Before every move, ask: what is my opponent's plan? Black would like ...Nb4 or ...Bb4 putting pressure on White's structure.",
                        "a3 — prophylaxis! This quiet move prevents ...Nb4 and ...Bb4. At higher levels, preventing your opponent's best moves is just as important as executing your own plan.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.2",
                "title": "King Attack Technique",
                "concept": "King Safety",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2PNP3/2N1BP2/PP4PP/R2QKB1R w KQ - 0 7",
                        "best_move": "Qd2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2PNP3/2N1BP2/PP4PP/R2QKB1R w KQ - 0 7",
                    "moves": ["Qd2"],
                    "annotations": [
                        "White is building a Yugoslav Attack position. The plan: Qd2, Bh6 to trade Black's fianchettoed bishop, then O-O-O and push h4-h5 against the castled king.",
                        "Qd2 — prepares Bh6 to exchange the dark-squared bishop. When attacking a fianchettoed king, removing the g7 bishop is step one. Then the dark squares around the king become indefensible.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.3",
                "title": "Rook Lifts",
                "concept": "Rook On Open File",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2P1P3/2N1BN2/PP2BPPP/R2Q1RK1 w - - 0 7",
                        "best_move": "Qd2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2P1P3/2N1BN2/PP2BPPP/2RQ1RK1 w - - 0 8",
                    "moves": ["Nd5"],
                    "annotations": [
                        "A rook lift means swinging a rook from its starting file to an attacking file via the 3rd or 4th rank (e.g. Rf3-h3 or Ra1-a3-h3).",
                        "Nd5 — While this particular move prepares central play, the concept of rook lifts is about getting rooks into the attack without an open file. Think Rf1-f3-h3 or Ra1-a3-g3 to create threats against the king.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.4",
                "title": "IQP Plans",
                "concept": "Isolated Pawn",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp2ppbp/2np1np1/8/2PP4/2N2N2/PP2BPPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "d5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["Qe2"],
                    "annotations": [
                        "With an Isolated Queen Pawn (IQP) on d4, White's plans are: Nd5 outpost, kingside attack via Qe2+Rd1+Bc2, or d4-d5 pawn break. The IQP gives active piece play in exchange for a static weakness.",
                        "Qe2 — the queen supports both the e4 outpost and prepares to swing to the kingside. IQP positions are about activity: if the game simplifies too much, the isolated pawn becomes weak. Keep the pressure on!",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.5",
                "title": "Converting Advantages",
                "concept": "Centralization",
                "positions": [
                    {
                        "fen": "2r2rk1/pp3ppp/2p1pn2/q7/3P4/2P1PN2/P3QPPP/R4RK1 w - - 0 12",
                        "best_move": "Nd2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "2r2rk1/pp3ppp/2p1pn2/q7/3P4/2P1PN2/P3QPPP/R4RK1 w - - 0 12",
                    "moves": ["Nd2"],
                    "annotations": [
                        "When you have an advantage, don't rush. The correct technique: improve your worst-placed piece, restrict your opponent's counterplay, then strike when optimally positioned.",
                        "Nd2 — the knight reroutes to a better square (perhaps c4 or f3-g5). Converting an advantage requires patience: centralize pieces, limit counterplay, then choose the right moment to break through.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.6",
                "title": "Deep Calculation",
                "concept": "Deflection",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp3ppp/2n1pn2/2bp4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "dxc5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/pp3ppp/2n1pn2/2bp4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["dxc5"],
                    "annotations": [
                        "Calculation isn't about seeing more moves — it's about seeing the RIGHT moves. Use candidate moves: identify 2-3 promising options, then calculate each one systematically.",
                        "dxc5 — open the d-file for the rook, gain a tempo on the bishop. When calculating: checks first, then captures, then threats. Always ask what your opponent's best reply is, not just your own plans.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.7",
                "title": "Opposite-Color Bishop Attack",
                "concept": "Bad Bishop",
                "positions": [
                    {
                        "fen": "r2q1rk1/pp2bppp/2n1pn2/2Bp4/4P3/2N2N2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                        "best_move": "Bg5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/pp2bppp/2n1pn2/2Bp4/4P3/2N2N2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                    "moves": ["Bg5"],
                    "annotations": [
                        "Opposite-colored bishops (one light-squared, one dark-squared) are drawish in endgames but ATTACKING weapons in middlegames. Your bishop controls squares the opponent's bishop cannot defend.",
                        "Bg5 — placing the bishop where the opponent's bishop has no influence. In middlegames with OCBs, the attacker has an extra piece for the attack since the defender's bishop cannot help defend the right-colored squares.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "6.8",
                "title": "Complex Rook Endgames",
                "concept": "Rook On Seventh Rank",
                "positions": [
                    {
                        "fen": "8/1p3kpp/p3rp2/4R3/1PP5/P5PP/5PK1/8 w - - 0 1",
                        "best_move": "b5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/1p3kpp/p3rp2/4R3/1PP5/P5PP/5PK1/8 w - - 0 1",
                    "moves": ["b5"],
                    "annotations": [
                        "In rook endgames, active rooks and passed pawns are the key themes. White should create a passed pawn while keeping the rook active.",
                        "b5 — creating a passed pawn on the queenside. Rook endgames are drawn most often, so you need concrete plans: create a passed pawn, activate your rook, and use your king aggressively.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 7: Expert (1600–1800) ───────────────────────────────────────────
    7: {
        "name": "Expert",
        "rating_band": "1600–1800",
        "rating_range": (1600, 1800),
        "description": "Advanced strategic concepts and endgame mastery",
        "modules": [
            {
                "id": "7.1",
                "title": "Exchange Sacrifices",
                "concept": "Initiative",
                "positions": [
                    {
                        "fen": "r2q1rk1/1pp1bppp/p1n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 7",
                        "best_move": "cxd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/1pp1bppp/p1n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 7",
                    "moves": ["cxd5"],
                    "annotations": [
                        "An exchange sacrifice (giving a rook for a minor piece) can be strategically winning when the minor piece dominates or the opponent's rooks have no open files.",
                        "cxd5 — opening lines for the bishops. Petrosian-style exchange sacrifices aim for positional dominance: a strong knight on d5, control of key squares, and restricted enemy rooks.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "7.2",
                "title": "Maneuvering",
                "concept": "Piece Activity",
                "positions": [
                    {
                        "fen": "r2qr1k1/1pp1bppp/p1n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                        "best_move": "Ne5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2qr1k1/1pp1bppp/p1n1pn2/3p4/3P4/2NBPN2/PPP2PPP/R1BQ1RK1 w - - 0 8",
                    "moves": ["Ne5"],
                    "annotations": [
                        "Maneuvering is the art of slowly improving your position when no immediate breakthrough exists. Reposition pieces to their optimal squares without committing to a specific plan too early.",
                        "Ne5 — the knight heads to its best square, controlling key territory. In maneuvering positions, patience is essential: improve each piece one at a time, and your opponent's position will gradually crumble.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "7.3",
                "title": "Fortress Concepts",
                "concept": "Zugzwang",
                "positions": [
                    {
                        "fen": "8/8/1p6/1Pk5/2P5/1K6/8/8 b - - 0 1",
                        "best_move": "Kd6",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/1p6/1Pk5/2P5/1K6/8/8 b - - 0 1",
                    "moves": ["Kd6"],
                    "annotations": [
                        "A fortress is a defensive setup that the stronger side cannot break through despite having a material advantage. Recognizing fortress patterns can save otherwise lost positions.",
                        "Kd6 — Black maintains the blockade. As long as Black's king stays near the pawns, White cannot make progress. The c4 and b5 pawns are mutually blocked. This is a theoretical draw.",
                    ],
                    "player_color": "black",
                },
            },
            {
                "id": "7.4",
                "title": "Weakness Creation",
                "concept": "Two Weaknesses",
                "positions": [
                    {
                        "fen": "r2q1rk1/ppp1bppp/2n1pn2/3p4/3P1B2/2NBPN2/PPP2PPP/R2Q1RK1 w - - 0 7",
                        "best_move": "Qe2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/ppp1bppp/2n1pn2/3p4/3P1B2/2NBPN2/PPP2PPP/R2Q1RK1 w - - 0 7",
                    "moves": ["Qe2"],
                    "annotations": [
                        "The principle of two weaknesses: if your opponent has only one weakness, they can usually defend it. Create a SECOND weakness on the other side of the board to overstretch their defenses.",
                        "Qe2 — preparing pressure on both flanks. The defender's pieces cannot cover both sides of the board simultaneously. First create one weakness, then open a second front.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "7.5",
                "title": "Defensive Technique",
                "concept": "Prophylaxis",
                "positions": [
                    {
                        "fen": "r2q1rk1/1pp2ppp/p1nbpn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 b - - 0 7",
                        "best_move": "dxc4",
                        "player_color": "black",
                    },
                ],
                "walkthrough": {
                    "fen": "r2q1rk1/1pp2ppp/p1nbpn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 b - - 0 7",
                    "moves": ["dxc4"],
                    "annotations": [
                        "Good defense is not passive — it involves simplification, counterattack, and fortress-building. When worse, reduce your opponent's attacking potential.",
                        "dxc4 — Black trades pawns to simplify the position. When defending: exchange attacking pieces, keep defensive pieces, create counterplay, and never allow your opponent to attack for free.",
                    ],
                    "player_color": "black",
                },
            },
            {
                "id": "7.6",
                "title": "Zugzwang Patterns",
                "concept": "Zugzwang",
                "positions": [
                    {
                        "fen": "8/8/1k6/1p6/1P6/1K6/8/8 w - - 0 1",
                        "best_move": "Ka3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/1k6/1p6/1P6/1K6/8/8 w - - 0 1",
                    "moves": ["Ka3"],
                    "annotations": [
                        "Zugzwang is when any move worsens your position, but you must move. It's most common in endgames where every tempo matters.",
                        "Ka3 — White gives Black the move. Now every Black king move either allows White's king to advance or loses the b5 pawn. This is mutual zugzwang: whoever has to move loses. Understanding zugzwang is key to king and pawn endgames.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "7.7",
                "title": "Minor Piece Endgames",
                "concept": "Bishop vs Knight",
                "positions": [
                    {
                        "fen": "8/pp3kpp/4p3/3pP3/3P2P1/4BK2/PP5P/3n4 w - - 0 1",
                        "best_move": "Bd2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/pp3kpp/4p3/3pP3/3P2P1/4BK2/PP5P/3n4 w - - 0 1",
                    "moves": ["Bd2"],
                    "annotations": [
                        "Bishop vs knight endgames depend on the pawn structure. Bishops prefer open positions with pawns on both flanks. Knights prefer closed positions with fixed targets.",
                        "Bd2 — White's bishop controls both sides of the board while the knight is limited in range. In minor piece endgames, the side with the bishop should open the position and create passed pawns on opposite flanks.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },

    # ── Stage 8: Mastery (1800–2000+) ─────────────────────────────────────────
    8: {
        "name": "Mastery",
        "rating_band": "1800–2000+",
        "rating_range": (1800, 9999),
        "description": "Master-level strategy, calculation, and endgame precision",
        "modules": [
            {
                "id": "8.1",
                "title": "Long-Term Planning",
                "concept": "Space Advantage",
                "positions": [
                    {
                        "fen": "r1bq1rk1/pp1n1ppp/2p1pn2/3p4/2PP4/1QN1PN2/PP3PPP/R1B2RK1 w - - 0 7",
                        "best_move": "Bd2",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/pp1n1ppp/2p1pn2/3p4/2PP4/1QN1PN2/PP3PPP/R1B2RK1 w - - 0 7",
                    "moves": ["Bd2"],
                    "annotations": [
                        "Long-term planning means identifying a favorable transformation 10-15 moves ahead and slowly maneuvering toward it. At master level, every move serves a multi-move plan.",
                        "Bd2 — a quiet developing move that keeps all options open. White's long-term plan involves a kingside minority attack or central break. The key is: have a plan, execute it patiently, and adjust when your opponent forces you to.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "8.2",
                "title": "Dynamic Compensation",
                "concept": "Initiative",
                "positions": [
                    {
                        "fen": "r1bq1rk1/ppp2ppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R1BQKB1R w KQ - 0 5",
                        "best_move": "Bd3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/ppp2ppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R1BQKB1R w KQ - 0 5",
                    "moves": ["Bd3"],
                    "annotations": [
                        "Dynamic compensation means having intangible advantages (activity, initiative, attacking chances) that offset material deficit. Evaluating these positions requires deep understanding.",
                        "Bd3 — developing while maintaining tension. In positions with dynamic compensation, the key is to maintain the initiative. If you let the opponent consolidate, the material deficit will tell.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "8.3",
                "title": "Risk Management",
                "concept": "Prophylaxis",
                "positions": [
                    {
                        "fen": "r1bq1rk1/ppp1bppp/2n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 6",
                        "best_move": "cxd5",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "r1bq1rk1/ppp1bppp/2n1pn2/3p4/2PP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 6",
                    "moves": ["cxd5"],
                    "annotations": [
                        "At the highest levels, risk management — choosing between sharp and safe continuations based on the match situation — separates good players from great ones.",
                        "cxd5 — simplifying when ahead or maintaining tension when behind. The best players choose their level of risk based on the tournament situation, time on the clock, and their opponent's tendencies.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "8.4",
                "title": "Advanced Endgame Technique",
                "concept": "Opposition",
                "positions": [
                    {
                        "fen": "8/8/4k3/3p1p2/3P1P2/4K3/8/8 w - - 0 1",
                        "best_move": "Kd3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "8/8/4k3/3p1p2/3P1P2/4K3/8/8 w - - 0 1",
                    "moves": ["Kd3"],
                    "annotations": [
                        "Advanced endgame technique combines opposition, triangulation, corresponding squares, and zugzwang into a unified approach for complex pawn endings.",
                        "Kd3 — gaining the opposition on the queenside. By understanding corresponding squares (which squares your king must occupy relative to the opponent's king), you can navigate even the most complex pawn endings correctly.",
                    ],
                    "player_color": "white",
                },
            },
            {
                "id": "8.5",
                "title": "Converting Small Advantages",
                "concept": "Centralization",
                "positions": [
                    {
                        "fen": "2r2rk1/pp2bppp/2n1pn2/q2p4/3P4/2NBPN2/PPQ2PPP/R4RK1 w - - 0 10",
                        "best_move": "a3",
                        "player_color": "white",
                    },
                ],
                "walkthrough": {
                    "fen": "2r2rk1/pp2bppp/2n1pn2/q2p4/3P4/2NBPN2/PPQ2PPP/R4RK1 w - - 0 10",
                    "moves": ["a3"],
                    "annotations": [
                        "The hardest skill in chess: converting a small, stable advantage into a win. The process: improve your pieces, restrict counterplay, create a second weakness, then break through.",
                        "a3 — a prophylactic move securing the queenside. When you have a small edge, don't rush. Improve everything first, then strike when the position is maximally favorable. Patience wins games.",
                    ],
                    "player_color": "white",
                },
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_stage_for_rating(rating: int) -> int:
    """Map a Chess.com rating to the recommended curriculum stage (1-8)."""
    for stage_num in range(8, 0, -1):
        low, _ = CURRICULUM[stage_num]["rating_range"]
        if rating >= low:
            return stage_num
    return 1


# Phase → concept mapping for phase-weakness recommendations
_PHASE_CONCEPTS = {
    "opening": {"Centralization", "Piece Activity", "King Safety", "Back Rank Weakness"},
    "middlegame": {"Fork", "Pin", "Discovered Attack", "Deflection", "Overloading",
                   "Trapped Piece", "Initiative", "Space Advantage", "Outpost",
                   "King Safety", "Rook On Open File"},
    "endgame": {"Opposition", "Lucena Position", "Philidor Position", "Zugzwang",
                "Passed Pawn", "Rook On Seventh Rank", "Bishop vs Knight",
                "Two Weaknesses"},
}

# Skill category → concept mapping
_SKILL_CONCEPTS = {
    "Tactics": {"Fork", "Pin", "Discovered Attack", "Deflection", "Overloading",
                "Trapped Piece", "Zwischenzug", "Double Check", "Decoy"},
    "Opening Prep": {"Centralization", "Piece Activity", "King Safety",
                     "Back Rank Weakness", "Space Advantage"},
    "Middlegame": {"Initiative", "Outpost", "Rook On Open File", "Minority Attack",
                   "Prophylaxis", "Bad Bishop", "Isolated Pawn", "Bishop Pair"},
    "Endgame": {"Opposition", "Lucena Position", "Philidor Position", "Zugzwang",
                "Passed Pawn", "Rook On Seventh Rank", "Bishop vs Knight",
                "Two Weaknesses"},
    "Piece Activity": {"Piece Activity", "Centralization", "Rook On Open File",
                       "Rook On Seventh Rank", "Outpost"},
}


def get_recommended_modules(
    profile_data: dict | None,
    profile_summaries: list[dict] | None,
    rating: int | None = None,
    max_results: int = 5,
) -> list[dict]:
    """
    Return up to *max_results* recommended modules based on player weaknesses.

    Each returned dict: {module_id, stage, title, concept, reason}

    Scoring:
      - priority_focus concept match  → +3
      - weak skill_rating (<=2) concept match → +2
      - weakest phase concept match   → +1
      - stage near rating-appropriate stage → +1 (within ±1)

    Modules the player has already completed are deprioritised (score -2).
    """
    if not profile_data:
        return []

    # ── Collect weak concepts with scores ────────────────────────────────────
    concept_scores: dict[str, float] = {}

    # 1. priority_focus concepts (strongest signal)
    for concept in profile_data.get("priority_focus", []):
        concept_scores[concept] = concept_scores.get(concept, 0) + 3

    # 2. Weak skill categories (rating <= 2 out of 5)
    for cat, info in profile_data.get("skill_ratings", {}).items():
        r = info.get("rating", 3) if isinstance(info, dict) else 3
        if r <= 2 and cat in _SKILL_CONCEPTS:
            for concept in _SKILL_CONCEPTS[cat]:
                concept_scores[concept] = concept_scores.get(concept, 0) + 2

    # 3. Weakest phase from summaries
    if profile_summaries:
        def _avg(vals):
            clean = [v for v in vals if v is not None]
            return sum(clean) / len(clean) if clean else 50.0

        phase_accs = {
            "opening": _avg([s.get("opening_accuracy") for s in profile_summaries]),
            "middlegame": _avg([s.get("middlegame_accuracy") for s in profile_summaries]),
            "endgame": _avg([s.get("endgame_accuracy") for s in profile_summaries]),
        }
        weakest = min(phase_accs, key=phase_accs.get)
        for concept in _PHASE_CONCEPTS.get(weakest, set()):
            concept_scores[concept] = concept_scores.get(concept, 0) + 1

    # 4. Concept mastery from puzzle performance (low accuracy → boost priority)
    try:
        import db as _db
        _cm = _db.get_all_concept_mastery()
        for c_name, c_data in _cm.items():
            if c_data["attempted"] >= 3 and c_data["pct"] < 60:
                concept_scores[c_name] = concept_scores.get(c_name, 0) + 2
            elif c_data["attempted"] >= 3 and c_data["pct"] < 80:
                concept_scores[c_name] = concept_scores.get(c_name, 0) + 1
    except Exception:
        pass

    if not concept_scores:
        return []

    # ── Score every module ───────────────────────────────────────────────────
    target_stage = get_stage_for_rating(rating) if rating else None

    scored: list[tuple[float, dict]] = []
    for stage_num, stage in CURRICULUM.items():
        for mod in stage["modules"]:
            concept = mod.get("concept", "")
            score = concept_scores.get(concept, 0)
            if score <= 0:
                continue

            # Prefer modules near the player's level
            if target_stage and abs(stage_num - target_stage) <= 1:
                score += 1

            reason_parts = []
            if concept in [c for c in profile_data.get("priority_focus", [])]:
                reason_parts.append("priority focus area")
            for cat, info in profile_data.get("skill_ratings", {}).items():
                r = info.get("rating", 3) if isinstance(info, dict) else 3
                if r <= 2 and cat in _SKILL_CONCEPTS and concept in _SKILL_CONCEPTS[cat]:
                    reason_parts.append(f"weak {cat.lower()}")
                    break
            if not reason_parts:
                reason_parts.append("targets your weakest phase")

            scored.append((score, {
                "module_id": mod["id"],
                "stage": stage_num,
                "title": mod["title"],
                "concept": concept,
                "reason": reason_parts[0],
                "score": score,
            }))

    # Sort by score desc, then stage proximity to target
    scored.sort(key=lambda x: (-x[0], abs((x[1]["stage"] - target_stage) if target_stage else 0)))
    return [item for _, item in scored[:max_results]]


def build_guided_path(
    profile_data: dict | None,
    profile_summaries: list[dict] | None,
    completed: dict[str, dict] | None = None,
    rating: int | None = None,
) -> list[dict]:
    """
    Build a full ordered learning path based on the player's weaknesses.

    Returns ALL curriculum modules in recommended order.  Each dict:
        {module_id, stage, title, concept, reason, score, completed}

    Order:
      1. Priority focus + weak skill modules (highest relevance score)
      2. Modules at or near the player's current stage
      3. Remaining modules in stage order

    Already-completed modules are pushed to the end.
    """
    completed = completed or {}
    target_stage = get_stage_for_rating(rating) if rating else 1

    # ── Score every module using the same logic as get_recommended_modules ──
    concept_scores: dict[str, float] = {}
    if profile_data:
        for concept in profile_data.get("priority_focus", []):
            concept_scores[concept] = concept_scores.get(concept, 0) + 3
        for cat, info in profile_data.get("skill_ratings", {}).items():
            r = info.get("rating", 3) if isinstance(info, dict) else 3
            if r <= 2 and cat in _SKILL_CONCEPTS:
                for concept in _SKILL_CONCEPTS[cat]:
                    concept_scores[concept] = concept_scores.get(concept, 0) + 2

    if profile_summaries:
        def _avg(vals):
            clean = [v for v in vals if v is not None]
            return sum(clean) / len(clean) if clean else 50.0
        phase_accs = {
            "opening": _avg([s.get("opening_accuracy") for s in profile_summaries]),
            "middlegame": _avg([s.get("middlegame_accuracy") for s in profile_summaries]),
            "endgame": _avg([s.get("endgame_accuracy") for s in profile_summaries]),
        }
        weakest = min(phase_accs, key=phase_accs.get)
        for concept in _PHASE_CONCEPTS.get(weakest, set()):
            concept_scores[concept] = concept_scores.get(concept, 0) + 1

    all_modules: list[dict] = []
    for stage_num, stage in CURRICULUM.items():
        for mod in stage["modules"]:
            concept = mod.get("concept", "")
            score = concept_scores.get(concept, 0)

            # Prefer modules near the player's level
            stage_dist = abs(stage_num - target_stage) if target_stage else 0
            if stage_dist <= 1:
                score += 1

            reason = "curriculum"
            if concept in (profile_data or {}).get("priority_focus", []):
                reason = "priority focus area"
            elif score >= 2:
                reason = "targets your weaknesses"
            elif stage_dist <= 1:
                reason = "at your level"

            is_done = mod["id"] in completed and completed[mod["id"]].get("completed")

            all_modules.append({
                "module_id": mod["id"],
                "stage": stage_num,
                "title": mod["title"],
                "concept": concept,
                "reason": reason,
                "score": score,
                "completed": bool(is_done),
            })

    # Sort: incomplete first, then by score desc, then by stage proximity
    all_modules.sort(key=lambda m: (
        m["completed"],           # False (0) before True (1)
        -m["score"],              # higher score first
        abs(m["stage"] - target_stage) if target_stage else 0,
        m["stage"],               # within same distance, lower stage first
    ))

    return all_modules


def get_module(module_id: str) -> dict | None:
    """Look up a module by its ID (e.g. '3.2'). Returns None if not found."""
    try:
        stage_num = int(module_id.split(".")[0])
    except (ValueError, IndexError):
        return None
    stage = CURRICULUM.get(stage_num)
    if not stage:
        return None
    for mod in stage["modules"]:
        if mod["id"] == module_id:
            return mod
    return None


def build_module_puzzles(
    module: dict,
    profile_summaries: list | None = None,
    n: int = 5,
) -> list[dict]:
    """
    Build puzzle dicts for a training module.

    Curated positions are always included first, then topped up from the
    player's game positions (via profile_summaries) that match the concept.

    Returns dicts compatible with _interactive_board_html / _build_puzzle_phases:
        {fen, best_move_san, player_color, eval_before, eval_after,
         classification, hint, phases}
    """
    puzzles: list[dict] = []

    # 1. Curated positions
    for pos in module.get("positions", []):
        puzzles.append({
            "fen":           pos["fen"],
            "best_move_san": pos["best_move"],
            "player_color":  pos["player_color"],
            "eval_before":   0.0,
            "eval_after":    0.0,
            "classification": "",
            "hint":          None,
            "phases":        None,
        })

    if len(puzzles) >= n or not profile_summaries:
        return puzzles[:n]

    # 2. Supplement from player's games
    concept = module.get("concept", "")
    if not concept:
        return puzzles[:n]

    try:
        from chess_utils import position_has_concept as _position_has_concept
    except ImportError:
        return puzzles[:n]

    import chess.pgn
    import io as _io

    seen_fens = {p["fen"] for p in puzzles}
    for s in profile_summaries:
        if len(puzzles) >= n:
            break
        pgn_text = s.get("_pgn", "")
        if not pgn_text:
            continue
        moves_data = s.get("moves", [])
        for m in moves_data:
            if len(puzzles) >= n:
                break
            if m.get("classification") not in ("blunder", "mistake"):
                continue
            fen = m.get("fen_before", "")
            best = m.get("best_move_san", "")
            if not fen or not best or fen in seen_fens:
                continue
            try:
                _vboard = chess.Board(fen)
                if not _vboard.is_valid():
                    continue
                _vboard.parse_san(best)
            except Exception:
                continue
            color = m.get("color", "white")
            if _position_has_concept(fen, concept, best, color):
                seen_fens.add(fen)
                puzzles.append({
                    "fen":           fen,
                    "best_move_san": best,
                    "player_color":  color,
                    "eval_before":   m.get("eval_before", 0.0),
                    "eval_after":    m.get("eval_after", 0.0),
                    "classification": m.get("classification", ""),
                    "hint":          None,
                    "phases":        None,
                })

    return puzzles[:n]


def validate_curriculum() -> list[str]:
    """
    Validate every curated FEN and best_move in the curriculum.
    Returns a list of error strings (empty means all valid).
    """
    errors: list[str] = []
    for stage_num, stage in CURRICULUM.items():
        for mod in stage["modules"]:
            # Validate positions
            for i, pos in enumerate(mod.get("positions", [])):
                try:
                    board = chess.Board(pos["fen"])
                except ValueError as e:
                    errors.append(f"{mod['id']} pos[{i}]: invalid FEN — {e}")
                    continue
                if not board.is_valid():
                    status = board.status()
                    errors.append(f"{mod['id']} pos[{i}]: illegal position (status {status}) — {pos['fen']}")
                    continue
                try:
                    board.parse_san(pos["best_move"])
                except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError) as e:
                    errors.append(f"{mod['id']} pos[{i}]: illegal move {pos['best_move']} — {e}")

            # Validate walkthrough
            wt = mod.get("walkthrough")
            if wt:
                try:
                    board = chess.Board(wt["fen"])
                except ValueError as e:
                    errors.append(f"{mod['id']} walkthrough: invalid FEN — {e}")
                    continue
                if not board.is_valid():
                    status = board.status()
                    errors.append(f"{mod['id']} walkthrough: illegal position (status {status}) — {wt['fen']}")
                    continue
                for j, move_san in enumerate(wt["moves"]):
                    try:
                        mv = board.parse_san(move_san)
                        board.push(mv)
                    except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError) as e:
                        errors.append(f"{mod['id']} walkthrough move[{j}] {move_san}: illegal — {e}")
                        break

    return errors


# Run validation at import time (development safety net)
_validation_errors = validate_curriculum()
if _validation_errors:
    import warnings
    for _err in _validation_errors:
        warnings.warn(f"Curriculum validation: {_err}")
