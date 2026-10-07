"""Ponto de entrada: sobe o servidor local e abre a interface no navegador."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import __version__, config
from .server import make_server
from .state import AppState

BANNER = "\n  Faxina · controle de espaço\n  ───────────────────────────────"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="faxina", description="Descubra para onde foi o espaço do seu computador.")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, help="porta local (padrão: %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="não abrir o navegador automaticamente")
    parser.add_argument("--scan", metavar="PASTA", help="já começar analisando esta pasta")
    parser.add_argument("--version", action="version", version=f"Faxina {__version__}")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # acentos e o banner no console do Windows
    except (AttributeError, ValueError):
        pass

    state = AppState()
    server = make_server(state, args.port)
    if args.scan:
        try:
            state.start_scan(args.scan)
        except Exception as exc:
            print(f"  Não deu para analisar {args.scan}: {exc}")

    print(BANNER)
    print(f"  Faxina {__version__} rodando em {server.url}")
    print(f"  Dados e quarentena em: {state.data_dir}")
    print("  Para encerrar, use o botão Encerrar no app, feche esta janela ou aperte Ctrl+C.\n")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("\n  Até a próxima faxina!")


if __name__ == "__main__":
    main()
