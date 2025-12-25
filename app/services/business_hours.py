from datetime import time
from typing import Dict, List, Tuple

# Horarios por día de la semana (0 = lunes, 6 = domingo)
# Cada entrada es una lista de tuplas (inicio, fin) en horario local.
BUSINESS_HOURS: Dict[int, List[Tuple[time, time]]] = {
    0: [(time(8, 0), time(12, 0))],  # Lunes
    1: [(time(14, 0), time(17, 0))],  # Martes
    2: [(time(8, 0), time(12, 0)), (time(14, 0), time(17, 0))],  # Miércoles
    3: [(time(8, 0), time(12, 0))],  # Jueves
    4: [],  # Viernes
    5: [],  # Sábado
    6: [],  # Domingo
}

# Etiquetas para bloques si se quiere filtrar (morning/afternoon)
MORNING_END = time(12, 0)
