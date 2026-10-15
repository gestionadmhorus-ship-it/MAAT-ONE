# MAAT One

MAAT One es una aplicación local para Windows destinada a preparar los SET de
imágenes que posteriormente utilizará MAAT. Incluye revisión y selección manual, asociación de imágenes y exportación de anomalías.

## Buscador por coordenadas

El botón **Buscador por coordenadas** es un flujo independiente del armado de
SET. Permite seleccionar cualquier carpeta raíz, ingresar latitud, longitud y
un radio en metros (1 m por defecto), revisar las imágenes encontradas y
excluir las que no se deseen. Si no hay resultados, ofrece usar la imagen GPS
más cercana como nueva referencia.

Al guardar crea una subcarpeta `Búsqueda coordenadas <fecha-hora>` dentro de
la raíz elegida. Contiene las imágenes seleccionadas, un KMZ autocontenido
para abrir en Google Earth y un resumen de los parámetros usados. Solo se
incluyen imágenes con metadatos GPS válidos; las coordenadas se usan con toda
la precisión ingresada, sin redondearlas.

## Requisitos

- Windows
- Python 3.10 o superior
- Tkinter (incluido en la instalación estándar de Python para Windows)
- `python-calamine==0.8.2` (lectura de archivos `.xlsx` y `.xls`)

Instalar la dependencia con:

```text
python -m pip install -r requirements.txt
```

## Ejecución

Desde la carpeta del proyecto, ejecutar:

```text
python app.py
```

## Carga y procesamiento

Seleccione una raíz con nombres libres y exactamente un Excel de entrada `.xlsx`
o `.xls`, en cualquier subcarpeta. Se lee su primera hoja y se detecta la fila de
encabezados (fila 2 en el formato habitual). Los registros siguientes usan la
fecha de A y la hora HHMMSS de G, sin usar el nombre del Excel ni la columna B.
Se admiten horas como texto o número, recuperando ceros iniciales (81530 =
08:15:30). Se omiten filas vacías, cabeceras generales y el cierre del relevamiento.
Las filas con fecha u hora inválida se muestran como incidencias y deben corregirse
antes de exportar. Las fotos zoom se cotejan únicamente dentro del mismo día;
se mantienen las tolerancias de ±5 segundos para zoom y ±3 para W/T.

Las imágenes JPG se buscan en toda la raíz. Las salidas se guardan directamente
junto al Excel original: carpeta Anomalias o Anomalias Separadas, Anomalias.xlsx
y Trazabilidad MAAT One <proyecto>.xlsx. No se crea carpeta Resultados ni archivo
auxiliar visible. Los Excel llevan una identificación interna que permite excluir
los resultados al recargar. Se mantiene la compatibilidad con salidas antiguas
que tienen marcador JSON dentro de su propia carpeta.

La exportación prepara el contenido en una carpeta temporal. Ante fallos
controlados se revierte lo publicado por ese intento. Si alguno de los destinos
ya existe, se pide mover los resultados anteriores fuera de la raíz; no se
sobrescriben archivos existentes ni el Excel de entrada.

## Modos de salida y avance

Al confirmar la exportación se elige Anomalias Unificadas (carpeta Anomalias)
o Anomalias Separadas (una subcarpeta por SET, con el nombre de la anomalía).
Los nombres repetidos incorporan el ID de SET. Los caracteres no admitidos por
Windows se reemplazan. La hoja CARPETAS de la trazabilidad registra las rutas.
Durante la carga, asociación, búsqueda GPS y exportación se muestra una ventana
modal de avance y espera; la copia indica el progreso por imágenes.

El Excel Anomalias.xlsx registra FECHA, HORA DE ARCHIVO y NUMERO DE ARCHIVO
obtenidos de la Zoom finalmente elegida y copiada. Los demás datos provienen
del registro original. La trazabilidad conserva la referencia original y las
 diferencias con la imagen aprobada. Aplica a ambos modos de salida.

## Instalación en otra PC (Windows 10/11 x64)

Descargue `MAAT-One-1.1.0-Windows.zip` desde Releases, extraiga todo y abra
`MAAT-One-1.1.0-Setup.exe`. El asistente incluye Python y las dependencias; crea
accesos directos con icono en el escritorio y el menú Inicio. No requiere Python
preinstalado ni conexión durante la instalación. Instala solo para el usuario actual.
El ZIP sin `Windows` se conserva para el actualizador de instalaciones desde Python.

El código fuente y su historial permanecen en GitHub. Los proyectos, imágenes y
Excel del usuario no forman parte del instalador.
