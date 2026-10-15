"""Referencias manuales sin modificar el Excel de entrada."""
from datetime import datetime


def manual_record(image, data, number):
    return {
        "row_number": f"Manual-{number:04d}",
        "file_reference": image["time"],
        "date": datetime.strptime(image["date"], "%Y%m%d"),
        "manual_added": True,
        "original_data": {"elemento": data["element"], "anomalia": data["anomaly"], "criticidad": data["criticality"]},
        "export_data": {key: data[key] for key in ("element", "anomaly", "criticality")},
        "project_metadata": {key: data[key] for key in ("line", "stage")},
    }


def link_zoom(result, image):
    if not image.get("recognized") or image.get("channel") != "Z":
        raise ValueError("Se requiere una Zoom DJI válida.")
    if not result.get("normalized_date") or not result.get("target_time"):
        raise ValueError("Corrija la fecha u hora inválida del Excel antes de vincular imágenes.")
    target = datetime.strptime(result["normalized_date"] + result["target_time"], "%Y%m%d%H%M%S")
    actual = datetime.strptime(image["date"] + image["time"], "%Y%m%d%H%M%S")
    if result.get("zoom"):
        result.setdefault("original_suggested_zoom", result["zoom"])
    result.update(zoom=image, finally_used_zoom=image, zoom_time=image["time"],
                  time_difference=int((actual-target).total_seconds()),
                  status="ZOOM_PENDING_REVIEW", validation="REVIEW_REQUIRED", approved=None,
                  override_reason="Agregado manualmente" if result.get("manual_added") else "Zoom vinculada manualmente desde archivo")


def record_details(record, result):
    day = result.get("normalized_date")
    day = datetime.strptime(day, "%Y%m%d").strftime("%d/%m/%Y") if day else str(record.get("date", "Inválida"))
    hour = result.get("target_time")
    hour = f"{hour[:2]}:{hour[2:4]}:{hour[4:]}" if hour else str(record.get("file_reference", "Inválida"))
    source = "Agregado manualmente (sin fila Excel)" if record.get("manual_added") else f"Excel original · Fila {record.get('row_number', '?')}"
    data = record.get("original_data", {})
    return (f"{source}\nFecha: {day} · Hora: {hour}\n"
            f"Elemento: {data.get('elemento') or 'Sin dato'}\n"
            f"Anomalía: {data.get('anomalia') or 'Sin dato'}\n"
            f"Criticidad: {data.get('criticidad') or 'Sin dato'}")
