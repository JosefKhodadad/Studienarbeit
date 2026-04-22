"""Maschinelles Lernen für die Nacht/Tag-Klassifikation von Blutdruckmessungen.

Architekturüberblick
--------------------
Das Modul ist bewusst klein gehalten, aber sauber getrennt:

* :mod:`feature_engineering` baut deterministisch Features aus einer Liste von
  Messungen (zyklische Zeit, lokale Trends, BP-Drops, Messtyp-Hinweise, Alter).
* :mod:`night_model` definiert das Keras-MLP, Verlustfunktion, Callbacks und
  die Trainings-/Inferenz-Schnittstelle.
* :mod:`training_pipeline` lädt Roh-CSV-Daten (z. B. ``hilo_rohdaten_*.csv``),
  erzeugt schwache Labels und ruft das Modell auf.
* :mod:`constraint` setzt das Monats-Constraint aus dem PDF-Bericht durch.
* :mod:`sleep_phase` schätzt Einschlaf- und Aufwachzeiten zusätzlich zu der
  reinen Klassifikation.
* :mod:`model_store` kümmert sich um das Speichern/Laden der Gewichte und
  Metadaten.
* :mod:`training_jobs` führt Trainings im Hintergrund-Thread aus und stellt
  einen Status-Automat (idle/running/finished/failed) bereit.

Hinweis zum Backend
-------------------
Wir verwenden Keras 3 mit dem PyTorch-Backend. Die Keras-API ist identisch zu
TensorFlow, ein Wechsel wäre durch Setzen der Umgebungsvariable
``KERAS_BACKEND=tensorflow`` möglich, sofern TensorFlow installiert werden
kann (auf Windows-OneDrive-Pfaden ist das wegen Pfadlängenlimits aktuell
nicht der Fall — siehe README).
"""

from __future__ import annotations

import os

# Keras 3 wählt sein Backend zur Importzeit. Wir setzen ``torch`` als Default,
# bevor irgendein Submodul ``import keras`` ausführt. Eine bereits gesetzte
# Umgebungsvariable wird respektiert, damit CI/Tests den Wert überschreiben
# können.
os.environ.setdefault("KERAS_BACKEND", "torch")
