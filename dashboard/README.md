# Pulso TransMi — Dashboard MLOps (Bono de Visualización)

Dashboard interactivo y responsivo para el monitoreo en tiempo real de demanda, métricas de error (WAPE/Accuracy), estaciones de TransMilenio, estabilidad de variables (Drift PSI) y trazabilidad del pipeline en Supabase y GitHub Actions.

Diseñado con un enfoque **Mobile-First** (óptimo tanto en computadores como en teléfonos celulares).

---

## 📱 Características

* **KPIs Clave**: Accuracy acumulada ($87.21\%$), WAPE ($0.1279$), 12 estaciones oficiales y 51.840 observaciones.
* **Mapa Interactivo de Bogotá (Leaflet)**: Visualización geoespacial con marcadores coloreados e interactivos según el nivel de demanda de cada estación.
* **Series de Tiempo a 15 Minutos**: Comparación de demanda real vs predicciones de `ExtraTreesRegressor`.
* **Monitor de Data Drift**: Semáforo y barras de Population Stability Index (PSI) con umbrales de alerta ($> 0.20$).
* **MLOps Leaderboard**: Comparativa de modelos candidatos contra el Baseline estacional.
* **Modo Oscuro / Claro**: Selector integrado de tema visual.
* **Navegación Móvil Táctil**: Barra inferior optimizada para uso con el pulgar en celulares.

---

## 🚀 Cómo ejecutar localmente

Desde la carpeta raíz del proyecto:

```bash
# 1. (Opcional) Actualizar datos agregados desde el SDK
python3 dashboard/build_data.py

# 2. Iniciar servidor local
python3 -m http.server 8000 --directory dashboard
```

Abre en tu navegador (computador o celular en la misma red local):
👉 `http://localhost:8000`

---

## ☁️ Despliegue en Vercel (1 Clic)

1. Sube tu repositorio a GitHub.
2. Ve a [vercel.com](https://vercel.com) e inicia sesión con tu cuenta de GitHub.
3. Haz clic en **"Add New Project"** y selecciona tu repositorio `pulso-transmi-operador`.
4. En **Root Directory**, selecciona `dashboard`.
5. Haz clic en **Deploy**.

¡Vercel te entregará una URL pública y segura (HTTPS) para compartir tu dashboard desde cualquier celular o computador!
