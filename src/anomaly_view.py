"""Diagnóstico de todas las filas, sin aprobar ni modificar correlaciones."""
from src.set_builder import build_sets
from src.zoom_matcher import extract_target_time

def clock(value):
    return f"{value[:2]}:{value[2:4]}:{value[4:]}"

def describe_anomalies(results, images):
    rows = []
    for result in results:
        status = result["status"]
        preview = None
        if result.get("zoom"):
            # Esta copia sirve solo para previsualizar; no cambia aprobación ni exportación.
            preview = build_sets([{**result, "status": "ZOOM_ACCEPTED"}], images)[0]
        if status == "INVALID_REFERENCE":
            reason = "Fecha en A u hora HHMMSS en G inválida. Corrija esta fila del Excel."
            label = "Datos inválidos"
        elif status == "ZOOM_NOT_FOUND":
            label = "No encontrado"
            same_day = [i for i in images if i.get("recognized") and i.get("channel") == "Z" and i.get("date") == result["normalized_date"]]
            if not same_day:
                reason = "No hay imágenes Zoom de la fecha indicada en A."
            else:
                target = extract_target_time(result["file_reference"])[1]
                nearest = min(same_day, key=lambda i: abs(i["time_seconds"]-target))
                difference = nearest["time_seconds"]-target
                reason = f"Sin Zoom dentro de ±5 s. La más cercana es {clock(nearest['time'])} ({difference:+d} s)."
                reason += f" Horario disponible: {clock(min(i['time'] for i in same_day))} a {clock(max(i['time'] for i in same_day))}."
            reason += " Wide y Thermal no se pueden vincular sin una Zoom de referencia."
        elif status == "ZOOM_AMBIGUOUS":
            label = "Zoom ambigua"
            reason = f"Hay {len(result['candidates'])} Zoom igualmente cercanas; no se eligió una automáticamente."
        elif status == "ZOOM_REJECTED":
            label = "Rechazado"
            reason = "Zoom rechazada manualmente. Estas candidatas no se exportarán."
        elif status == "ZOOM_PENDING_REVIEW":
            label = "Pendiente de aprobación"
            reason = f"Zoom candidata a {result['time_difference']:+d} s del Excel. Revísela y acepte o rechace en el panel derecho."
        else:
            label = ("Confirmado manualmente" if status == "ZOOM_ACCEPTED" else "Coincidencia automática") + (" · completo" if preview and preview["status"] == "SET_COMPLETE" else " · incompleto")
            reason = "Zoom exacta aprobada automáticamente." if status == "ZOOM_EXACT" else "Zoom aprobada manualmente."
        if preview:
            for key, channel in (("wide", "W"), ("thermal", "T")):
                if channel in preview["ambiguous_channels"]:
                    reason += f" {channel}: varias candidatas igualmente cercanas."
                elif preview[key] is None:
                    reason += f" Falta {channel}: ninguna imagen de la misma fecha y secuencia dentro de ±3 s de Zoom."
            count = sum(preview[k] is not None for k in ("zoom", "wide", "thermal"))
        else:
            count = 0
        rows.append({"correlation": result, "preview": preview, "label": label, "reason": reason, "available": count})
    return rows
