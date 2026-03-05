"""
BoardSense - Step 1: Engine + Claude Analysis
Loads a chess position, asks Stockfish for the best move,
then asks Claude to explain it in plain English.
"""

import os
import sys
import chess
import chess.svg
from stockfish import Stockfish
from anthropic import Anthropic
from dotenv import load_dotenv

from engine import STOCKFISH_PATH  # cross-platform detection

# Allow printing of emoji and special characters on Windows
sys.stdout.reconfigure(encoding="utf-8")

# Load API key from .env file (local dev); Streamlit Cloud uses st.secrets
load_dotenv()

# How deeply Stockfish thinks (higher = stronger but slower, 10 is plenty for a tutor)
STOCKFISH_DEPTH = 10


def get_engine():
    """Start Stockfish and return the engine object."""
    return Stockfish(path=STOCKFISH_PATH, depth=STOCKFISH_DEPTH)


def analyze_position(fen: str) -> dict:
    """
    Given a FEN position string, return Stockfish's top moves and evaluation.
    FEN is the standard way to describe a chess position as text.
    """
    engine = get_engine()
    engine.set_fen_position(fen)

    # Get the top 3 candidate moves
    top_moves = engine.get_top_moves(3)

    # Get a text evaluation (e.g. "White is slightly better")
    evaluation = engine.get_evaluation()

    return {
        "fen": fen,
        "top_moves": top_moves,
        "evaluation": evaluation,
    }


def format_evaluation(evaluation: dict) -> str:
    """Turn Stockfish's raw evaluation into a readable string."""
    if evaluation["type"] == "cp":
        # Centipawns: 100 = 1 pawn advantage
        score = evaluation["value"] / 100
        if score > 0:
            return f"White is ahead by {score:.1f} pawns"
        elif score < 0:
            return f"Black is ahead by {abs(score):.1f} pawns"
        else:
            return "The position is equal"
    elif evaluation["type"] == "mate":
        moves = evaluation["value"]
        if moves > 0:
            return f"White has checkmate in {moves}"
        else:
            return f"Black has checkmate in {abs(moves)}"
    return "Unknown evaluation"


def ask_claude_to_explain(fen: str, best_move: str, evaluation_text: str, move_history: list[str]) -> str:
    """
    Ask Claude to explain the best move in plain English,
    as a chess tutor would to a beginner.
    """
    client = Anthropic()

    # Build a readable move history string
    history_text = ""
    if move_history:
        history_text = f"\nMoves played so far: {', '.join(move_history)}"

    prompt = f"""You are a friendly chess tutor helping a beginner learn the game.

Current position (FEN): {fen}{history_text}
Evaluation: {evaluation_text}
Best move: {best_move}

Please explain:
1. What the best move is (in plain English, e.g. "Move the knight from f3 to d4")
2. Why it's the best move — what does it accomplish?
3. What general chess principle it follows (if any), such as controlling the center, developing pieces, king safety, etc.

Keep your explanation friendly, clear, and under 150 words. Avoid jargon unless you explain it."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


def print_board(fen: str):
    """Print a simple text representation of the board."""
    board = chess.Board(fen)
    print("\n" + str(board) + "\n")


def main():
    print("=" * 50)
    print("       BOARDSENSE - Position Analyzer")
    print("=" * 50)

    # Start from the opening position (you can change this FEN to any position)
    fen = chess.STARTING_FEN
    board = chess.Board(fen)
    move_history = []

    print("\nStarting position loaded.")
    print_board(fen)

    print("Asking Stockfish to analyze the position...")
    result = analyze_position(fen)

    best_move_uci = result["top_moves"][0]["Move"]  # e.g. "e2e4"
    evaluation_text = format_evaluation(result["evaluation"])

    # Convert UCI move to readable format (e.g. "e2e4" -> "e4")
    move = chess.Move.from_uci(best_move_uci)
    best_move_san = board.san(move)  # Standard Algebraic Notation

    print(f"Evaluation: {evaluation_text}")
    print(f"Stockfish's best move: {best_move_san}")
    print("\nAsking Claude to explain the move...\n")

    explanation = ask_claude_to_explain(fen, best_move_san, evaluation_text, move_history)

    print("-" * 50)
    print("TUTOR SAYS:")
    print("-" * 50)
    print(explanation)
    print("-" * 50)

    # Show all top moves
    print("\nTop 3 moves Stockfish considered:")
    for i, m in enumerate(result["top_moves"], 1):
        uci = m["Move"]
        move_obj = chess.Move.from_uci(uci)
        san = board.san(move_obj)
        centipawns = m.get("Centipawn", "N/A")
        print(f"  {i}. {san}  (score: {centipawns})")


if __name__ == "__main__":
    main()
