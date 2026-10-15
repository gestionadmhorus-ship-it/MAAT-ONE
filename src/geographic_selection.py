"""Estado temporal de revisión para resultados geográficos."""


class GeographicSelection:
    """Mantiene resultados originales y una selección navegable separada."""

    def __init__(self) -> None:
        self.original_results: list[dict[str, object]] = []
        self.selected_results: list[dict[str, object]] = []
        self.index = 0

    @property
    def current(self) -> dict[str, object] | None:
        if not self.selected_results:
            return None
        return self.selected_results[self.index]

    @property
    def original_count(self) -> int:
        return len(self.original_results)

    @property
    def selected_count(self) -> int:
        return len(self.selected_results)

    def replace(self, results: list[dict[str, object]]) -> None:
        """Reemplaza por completo el resultado y selecciona todos sus ítems."""
        self.original_results = [dict(result) for result in results]
        self.selected_results = [dict(result) for result in results]
        self.index = 0

    def previous(self) -> dict[str, object] | None:
        if self.selected_results and self.index > 0:
            self.index -= 1
        return self.current

    def next(self) -> dict[str, object] | None:
        if self.selected_results and self.index < self.selected_count - 1:
            self.index += 1
        return self.current

    def remove_current(self) -> dict[str, object] | None:
        """Excluye sólo de la selección y conserva los resultados originales."""
        if not self.selected_results:
            return None
        removed = self.selected_results.pop(self.index)
        if self.index >= self.selected_count:
            self.index = max(self.selected_count - 1, 0)
        return removed

    def restore(self) -> None:
        """Restaura todos los resultados sin repetir la búsqueda."""
        self.selected_results = [dict(result) for result in self.original_results]
        self.index = 0
