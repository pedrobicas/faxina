"""Rode com:  python faxina.py   (ou dê dois cliques em iniciar.bat no Windows)"""
import sys

if sys.version_info < (3, 8):
    print("O Faxina precisa do Python 3.8 ou mais novo. Baixe em https://www.python.org/downloads/")
    input("Aperte Enter para sair...")
    sys.exit(1)

from faxina.app import main

if __name__ == "__main__":
    main()
