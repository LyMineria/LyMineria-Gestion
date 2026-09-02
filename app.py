# -*- coding: utf-8 -*-
import hmac
import os

import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime

# ------------------------------------------------------------------
# CONEXIÓN A POSTGRESQL
# ------------------------------------------------------------------
def obtener_conexion():
    """Abre una conexión usando secretos fuera del código fuente."""
    try:
        database_config = st.secrets.get("database", {})
    except Exception:
        database_config = {}
    connection_values = {
        "host": database_config.get("host", os.getenv("DB_HOST")),
        "database": database_config.get("database", os.getenv("DB_NAME", "postgres")),
        "user": database_config.get("user", os.getenv("DB_USER")),
        "password": database_config.get("password", os.getenv("DB_PASSWORD")),
        "port": database_config.get("port", os.getenv("DB_PORT", "5432")),
    }
    missing_values = [
        key for key in ("host", "user", "password")
        if not connection_values[key]
    ]
    if missing_values:
        raise RuntimeError(
            "Faltan credenciales de base de datos: " + ", ".join(missing_values)
        )

    conn = psycopg2.connect(**connection_values)
    conn.set_client_encoding('UTF8')
    return conn


def cerrar_sesion():
    st.session_state.pop("usuario_actual", None)
    st.session_state.pop("rol_usuario", None)
    st.rerun()


st.set_page_config(page_title="Gestión Logística y Minería", layout="wide")

# ------------------------------------------------------------------
# CONTROL DE ACCESO (LOGIN)
# ------------------------------------------------------------------
if 'usuario_actual' not in st.session_state:
    st.session_state.usuario_actual = None

if st.session_state.usuario_actual is None:
    st.subheader("🔒 Acceso Restringido - Sistema de Logística y Minería")
    user = st.text_input("Usuario")
    password = st.text_input("Contraseña", type="password")
    
    if st.button("Ingresar"):
        conn = None
        try:
            conn = obtener_conexion()
            cur = conn.cursor()
            cur.execute(
                "SELECT rol, password FROM usuarios WHERE nombre_usuario = %s",
                (user,),
            )
            res = cur.fetchone()
            credenciales_validas = bool(
                res and hmac.compare_digest(str(res[1]), password)
            )

            if credenciales_validas:
                st.session_state.usuario_actual = user
                st.session_state.rol_usuario = res[0]
                st.success("¡Bienvenido!")
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")
        except Exception as e:
            st.error(f"Error de conexión: {e}")
        finally:
            if conn is not None:
                conn.close()
            
    st.stop() # Frena la app acá para que no se muestre nada del sistema de fondo

# ------------------------------------------------------------------
# INICIO DEL SISTEMA (Solo visible tras iniciar sesión)
# ------------------------------------------------------------------
st.title("🚛 Sistema de gestión Logística y Minería")
header_col, logout_col = st.columns([6, 1])
with header_col:
    st.write(f"👤 Usuario conectado: **{st.session_state.usuario_actual}** ({st.session_state.rol_usuario})")
with logout_col:
    if st.button("Cerrar sesión"):
        cerrar_sesion()

tab_remitos, tab_flota, tab_reportes = st.tabs(
    ["📥 Remitos", "🚛 Flota", "📊 Reportes de Toneladas"]
)
# ------------------------------------------------------------------
# PESTAÑA 1: REMITOS (Orden de campos exacto)
# ------------------------------------------------------------------
with tab_remitos:
    st.header("Carga Manual de Remito")
    
    # Cargar choferes para el desplegable
    try:
        conn = obtener_conexion()
        df_choferes_list = pd.read_sql("SELECT id_chofer, nombre_completo FROM choferes WHERE estado = 'Activo' ORDER BY nombre_completo", conn)
        conn.close()
        opciones_choferes = {row['nombre_completo']: row['id_chofer'] for _, row in df_choferes_list.iterrows()}
    except Exception:
        opciones_choferes = {}

    with st.form("form_remito", clear_on_submit=True):
        # Fila 1: Campos 1 a 4 (Izquierda a Derecha)
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            num_remito = st.text_input("1. Número de Remito")
        with col2:
            fecha_viaje = st.date_input("2. Fecha de Viaje")
        with col3:
            chofer_nom = st.selectbox("3. Nombre Chofer", list(opciones_choferes.keys()) if opciones_choferes else ["Sin choferes"])
        with col4:
            toneladas = st.number_input("4. Toneladas", min_value=0.0, step=0.1)

        # Fila 2: Campos 5 a 8 (Izquierda a Derecha)
        col5, col6, col7, col8 = st.columns(4)
        with col5:
            material = st.selectbox("5. Material", ["Arena", "Piedra", "Tierra", "Mineral", "Otro"])
        with col6:
            origen = st.text_input("6. Origen")
        with col7:
            destino = st.text_input("7. Destino")
        with col8:
            proveedor = st.text_input("8. Proveedor")

        guardar = st.form_submit_button("💾 Guardar Remito")

        if guardar:
            if num_remito:
                try:
                    id_chof = opciones_choferes.get(chofer_nom)
                    conn = obtener_conexion()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO remitos (numero_remito_fisico, fecha_viaje, id_chofer, toneladas, material, origen, destino, proveedor)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (num_remito, fecha_viaje, id_chof, toneladas, material, origen, destino, proveedor))
                    conn.commit()
                    conn.close()
                    st.success(f"¡Remito N° {num_remito} guardado correctamente!")
                except Exception as e:
                    st.error(f"Error al guardar remito: {e}")
            else:
                st.warning("Debe ingresar el Número de Remito.")

# ------------------------------------------------------------------
# PESTAÑA 2: FLOTA (Asignaciones y Carga)
# ------------------------------------------------------------------
with tab_flota:
    st.header("Gestión de Flota y Personal")

    # 1. SECCIÓN PARA AGREGAR NUEVOS REGISTROS
    with st.expander("➕ Agregar nuevo integrante o vehículo a la empresa", expanded=False):
        tipo_alta = st.radio("Seleccione qué desea cargar:", ["Chofer", "Camión", "Batea"], horizontal=True)
        
        if tipo_alta == "Chofer":
            c1, c2 = st.columns(2)
            with c1:
                nom_chofer = st.text_input("Nombre Completo")
                dni_chofer = st.text_input("DNI")
            with c2:
                tel_chofer = st.text_input("Teléfono")
            if st.button("Guardar Chofer"):
                conn = obtener_conexion()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO choferes (nombre_completo, dni, telefono) VALUES (%s, %s, %s)", (nom_chofer, dni_chofer, tel_chofer))
                conn.commit()
                conn.close()
                st.success("Chofer agregado.")
                st.rerun()

        elif tipo_alta == "Camión":
            c1, c2 = st.columns(2)
            with c1:
                pat_camion = st.text_input("Patente Camión")
                mar_camion = st.text_input("Marca/Modelo")
            if st.button("Guardar Camión"):
                conn = obtener_conexion()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO camiones (patente, marca_modelo) VALUES (%s, %s)", (pat_camion, mar_camion))
                conn.commit()
                conn.close()
                st.success("Camión agregado.")
                st.rerun()

        elif tipo_alta == "Batea":
            c1, c2 = st.columns(2)
            with c1:
                pat_batea = st.text_input("Patente Batea")
                cap_batea = st.number_input("Capacidad (Tn)", min_value=0.0)
            if st.button("Guardar Batea"):
                conn = obtener_conexion()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO bateas (patente, capacidad) VALUES (%s, %s)", (pat_batea, cap_batea))
                conn.commit()
                conn.close()
                st.success("Batea agregada.")
                st.rerun()

    st.divider()

    # 2. CUADRO INTERACTIVO DE ASIGNACIONES (Con el signo +)
    st.subheader("🔗 Cuadro de Operaciones (Flota Activa)")
    st.caption("Hacé clic en el signo **+** (abajo de la tabla) para armar un nuevo equipo. Si querés desarmarlo, tildá la casilla de la izquierda y apretá la papelera.")

    try:
        conn = obtener_conexion()
        # Traer listas de elementos
        df_choferes = pd.read_sql("SELECT id_chofer, nombre_completo FROM choferes WHERE estado = 'Activo'", conn)
        df_bateas = pd.read_sql("SELECT id_batea, patente FROM bateas", conn)
        df_camiones = pd.read_sql("SELECT id_camion, patente FROM camiones", conn)
        
        # SIN ACENTOS EN LOS ALIAS DEL SQL (Evita el error utf-8 0xf3)
        df_asignados = pd.read_sql("""
            SELECT c.patente AS camion, b.patente AS batea, ch.nombre_completo AS chofer
            FROM camiones c
            LEFT JOIN bateas b ON c.id_batea_actual = b.id_batea
            LEFT JOIN choferes ch ON c.id_chofer_actual = ch.id_chofer
            WHERE c.id_chofer_actual IS NOT NULL OR c.id_batea_actual IS NOT NULL
        """, conn)
        
        # Listas para los desplegables
        lista_camiones = df_camiones['patente'].tolist()
        lista_bateas = df_bateas['patente'].tolist()
        lista_choferes = df_choferes['nombre_completo'].tolist()

        # Tabla editable (Acá le devolvemos el acento y los emojis para la vista)
        editor = st.data_editor(
            df_asignados,
            column_config={
                "camion": st.column_config.SelectboxColumn("🚛 Camión", options=lista_camiones, required=True),
                "batea": st.column_config.SelectboxColumn("🚜 Batea", options=lista_bateas, required=True),
                "chofer": st.column_config.SelectboxColumn("👤 Chofer", options=lista_choferes, required=True),
            },
            num_rows="dynamic", # Esto activa el signo +
            use_container_width=True,
            key="editor_flota"
        )

        if st.button("🔄 Guardar Asignaciones en Base de Datos"):
            cursor = conn.cursor()
            # 1. Limpiar todas las asignaciones (Reset)
            cursor.execute("UPDATE camiones SET id_batea_actual = NULL, id_chofer_actual = NULL")
            
            # 2. Aplicar las nuevas asignaciones del cuadro
            for index, row in editor.iterrows():
                if pd.notna(row['camion']) and pd.notna(row['batea']) and pd.notna(row['chofer']):
                    cursor.execute("""
                        UPDATE camiones 
                        SET id_batea_actual = (SELECT id_batea FROM bateas WHERE patente = %s),
                            id_chofer_actual = (SELECT id_chofer FROM choferes WHERE nombre_completo = %s)
                        WHERE patente = %s
                    """, (row['batea'], row['chofer'], row['camion']))
            
            conn.commit()
            st.success("¡Flota actualizada y en movimiento!")
            st.rerun()

        st.divider()

        # 3. LISTAS DE RECURSOS LIBRES (Ahora protegidos en el mismo bloque)
        st.subheader("🟢 Recursos Libres (En Base / Sin Asignar)")
        
        col_l1, col_l2, col_l3 = st.columns(3)
        
        with col_l1:
            st.markdown("**🚛 Camiones Libres**")
            camiones_libres = pd.read_sql("SELECT patente, marca_modelo FROM camiones WHERE id_chofer_actual IS NULL AND id_batea_actual IS NULL", conn)
            st.dataframe(camiones_libres, hide_index=True, use_container_width=True)

        with col_l2:
            st.markdown("**🚜 Bateas Libres**")
            bateas_libres = pd.read_sql("SELECT patente FROM bateas WHERE id_batea NOT IN (SELECT id_batea_actual FROM camiones WHERE id_batea_actual IS NOT NULL)", conn)
            st.dataframe(bateas_libres, hide_index=True, use_container_width=True)

        with col_l3:
            st.markdown("**👤 Choferes Libres**")
            choferes_libres = pd.read_sql("SELECT nombre_completo FROM choferes WHERE id_chofer NOT IN (SELECT id_chofer_actual FROM camiones WHERE id_chofer_actual IS NOT NULL) AND estado = 'Activo'", conn)
            st.dataframe(choferes_libres, hide_index=True, use_container_width=True)

    except Exception as e:
        st.error(f"Error de base de datos: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

# ------------------------------------------------------------------
# PESTAÑA 3: REPORTES DE TONELADAS (Filtros por Fecha, Quincena y Chofer)
# ------------------------------------------------------------------
with tab_reportes:
    st.header("Reportes y Acumulado de Toneladas")
    
    try:
        conn = obtener_conexion()
        query = """
            SELECT r.id_remito, r.numero_remito_fisico, r.fecha_viaje, 
                   ch.nombre_completo AS chofer, r.toneladas, r.material, 
                   r.origen, r.destino, r.proveedor
            FROM remitos r
            LEFT JOIN choferes ch ON r.id_chofer = ch.id_chofer
            ORDER BY r.fecha_viaje DESC
        """
        df_remitos = pd.read_sql(query, conn)
        conn.close()

        if not df_remitos.empty:
            df_remitos['fecha_viaje'] = pd.to_datetime(df_remitos['fecha_viaje'])
            
            col_f1, col_f2, col_f3 = st.columns(3)
            
            # Filtro por Chofer
            with col_f1:
                choferes_unicos = ["Todos"] + [c for c in df_remitos['chofer'].dropna().unique()]
                chofer_sel = st.selectbox("Filtrar por Chofer", choferes_unicos)

            # Filtro por Tipo de Período
            with col_f2:
                tipo_filtro = st.selectbox("Tipo de Período", ["Histórico Completo", "Diario", "Mensual", "Quincenal"])

            # Filtro Dinámico de Fechas
            df_filtrado = df_remitos.copy()
            
            if chofer_sel != "Todos":
                df_filtrado = df_filtrado[df_filtrado['chofer'] == chofer_sel]

            with col_f3:
                if tipo_filtro == "Diario":
                    fecha_sel = st.date_input("Seleccionar Día", datetime.now())
                    df_filtrado = df_filtrado[df_filtrado['fecha_viaje'].dt.date == fecha_sel]
                
                elif tipo_filtro == "Mensual":
                    mes_sel = st.selectbox("Seleccionar Mes", range(1, 13), index=datetime.now().month - 1)
                    anio_sel = st.number_input("Año", value=datetime.now().year)
                    df_filtrado = df_filtrado[(df_filtrado['fecha_viaje'].dt.month == mes_sel) & (df_filtrado['fecha_viaje'].dt.year == anio_sel)]

                elif tipo_filtro == "Quincenal":
                    quincena_sel = st.selectbox("Seleccionar Quincena", ["1ª Quincena (Días 1 a 15)", "2ª Quincena (Días 16 a fin)"])
                    mes_q = st.selectbox("Mes", range(1, 13), index=datetime.now().month - 1, key="mes_q")
                    anio_q = st.number_input("Año", value=datetime.now().year, key="anio_q")

                    df_mes = df_filtrado[(df_filtrado['fecha_viaje'].dt.month == mes_q) & (df_filtrado['fecha_viaje'].dt.year == anio_q)]
                    if "1ª Quincena" in quincena_sel:
                        df_filtrado = df_mes[df_mes['fecha_viaje'].dt.day <= 15]
                    else:
                        df_filtrado = df_mes[df_mes['fecha_viaje'].dt.day > 15]

            # Métrica de Toneladas Totales
            total_tn = df_filtrado['toneladas'].sum()
            st.metric(label=f"Total Toneladas ({tipo_filtro})", value=f"{total_tn:,.2f} Tn")

            # Tabla de Resultados
            st.dataframe(df_filtrado, use_container_width=True, hide_index=True)
        else:
            st.info("No hay remitos registrados en la base de datos todavía.")

    except Exception as e:
        st.error(f"Error al generar reportes: {e}")
