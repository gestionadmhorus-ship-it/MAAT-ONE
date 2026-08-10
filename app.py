"""Punto de entrada de MAAT One."""

from src.ui.main_window import MainWindow


def main() -> None:
    app = MainWindow()
    app.run()


if __name__ == "__main__":
    main()
