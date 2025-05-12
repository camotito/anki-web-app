import csv
import openai
import time
import os
import sys
import argparse
import langdetect
import logging
from typing import List, Dict, Optional, Tuple

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Obtener la API key desde la variable de entorno
api_key = os.environ.get("OPENAI_API_KEY")

# Verificar que la API key esté disponible
if not api_key:
    raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada. Por favor, configúrala antes de ejecutar el script.")

# Configurar el cliente de OpenAI con la API key
openai.api_key = api_key

def es_palabra_espanola(palabra: str) -> Tuple[bool, str]:
    """
    Detecta si una palabra está en español y devuelve el idioma detectado.
    Returns:
        Tuple[bool, str]: (True si es español, idioma detectado)
    """
    try:
        idioma = langdetect.detect(palabra)
        return (idioma == 'es', idioma)
    except Exception as e:
        logging.warning(f"Error al detectar idioma para '{palabra}': {str(e)}")
        return (False, 'unknown')

def es_palabra_inglesa(palabra: str) -> Tuple[bool, str]:
    """
    Detecta si una palabra está en inglés y devuelve el idioma detectado.
    Returns:
        Tuple[bool, str]: (True si es inglés, idioma detectado)
    """
    try:
        idioma = langdetect.detect(palabra)
        return (idioma == 'en', idioma)
    except Exception as e:
        logging.warning(f"Error al detectar idioma para '{palabra}': {str(e)}")
        return (False, 'unknown')

def inferir_traduccion(texto: str, es_espanol: bool) -> str:
    """
    Infiere la traducción más común de una palabra usando OpenAI.
    
    Args:
        texto: Palabra a traducir
        es_espanol: True si la palabra está en español y necesitamos traducción al inglés,
                   False si está en inglés y necesitamos traducción al español
    
    Returns:
        La traducción inferida
    """
    idioma_origen = "español" if es_espanol else "inglés"
    idioma_destino = "inglés" if es_espanol else "español"
    
    prompt = f"""
    Traduce la siguiente palabra del {idioma_origen} al {idioma_destino}.
    Dame solo la traduccion y nada mas de texto.
    
    Palabra: {texto}
    """
    
    try:
        # Añadir retry con backoff exponencial
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = openai.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Eres un traductor preciso que proporciona traducciones directas y concisas."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=50
                )
                
                traduccion = response.choices[0].message.content.strip()
                return traduccion
            except openai.RateLimitError:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 1  # Exponential backoff
                    logging.warning(f"Rate limit alcanzado, esperando {wait_time} segundos...")
                    time.sleep(wait_time)
                else:
                    raise
            
    except Exception as e:
        logging.error(f"Error al inferir traducción para '{texto}': {str(e)}")
        return ""

def procesar_csv(archivo_entrada: str, archivo_salida: str):
    """
    Procesa un archivo CSV con palabras en español e inglés, añadiendo definiciones
    solo cuando no existan y completando tipos gramaticales cuando sea necesario.
    """
    palabras_procesadas = []
    palabras_invertidas = 0
    # Ya no contamos definiciones agregadas
    traducciones_inferidas = 0
    palabras_sin_traducir = []
    palabras_idioma_incorrecto = []
    pares_duplicados = {}  # Diccionario para almacenar pares duplicados
    
    # Verificar que el archivo existe y no está vacío
    if not os.path.exists(archivo_entrada):
        raise FileNotFoundError(f"El archivo {archivo_entrada} no existe.")
    
    if os.path.getsize(archivo_entrada) == 0:
        raise ValueError(f"El archivo {archivo_entrada} está vacío.")
    
    # Leer el archivo CSV de entrada
    with open(archivo_entrada, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        # Obtener los nombres de las columnas
        fieldnames = reader.fieldnames
        
        # Verificar y preparar los nombres de columnas
        if not fieldnames:
            raise ValueError("No se pudieron leer los nombres de las columnas")
        
        # Determinar qué columnas usar basándose en los nombres disponibles
        col_espanol = next((col for col in fieldnames if col.lower() in ['español', 'espanol', 'spanish', 'palabra']), fieldnames[0])
        col_ingles = next((col for col in fieldnames if col.lower() in ['inglés', 'ingles', 'english', 'traduccion', 'traducción']), fieldnames[1])
        col_tipo = next((col for col in fieldnames if col.lower() in ['tipo', 'type', 'categoría', 'categoria', 'gramática', 'gramatica']), None)
        col_ejemplo = next((col for col in fieldnames if col.lower() in ['ejemplo', 'example', 'contexto']), None)
        # Ya no buscamos columna de definición
        
        # Leemos todas las filas
        for row in reader:
            palabra_esp = row[col_espanol].strip().lower()  # Normalizar para comparación
            palabra_ing = row[col_ingles].strip().lower()  # Normalizar para comparación
            
            # Registrar pares de palabras para detectar duplicados
            if palabra_esp and palabra_ing:
                par_key = (palabra_esp, palabra_ing)
                if par_key in pares_duplicados:
                    pares_duplicados[par_key].append(row)
                else:
                    pares_duplicados[par_key] = [row]
            
            # Verificar idiomas cuando ambas palabras están presentes
            if palabra_esp and palabra_ing:
                es_esp_valido, idioma_esp = es_palabra_espanola(palabra_esp)
                es_ing_valido, idioma_ing = es_palabra_inglesa(palabra_ing)
                
                if es_esp_valido and not es_ing_valido:
                    # Palabra en columna de inglés no es inglés
                    palabras_idioma_incorrecto.append((palabra_ing, "inglés", idioma_ing))
                elif es_ing_valido and not es_esp_valido:
                    # Palabra en columna de español no es español
                    palabras_idioma_incorrecto.append((palabra_esp, "español", idioma_esp))
                
                # Detectar si las palabras están invertidas
                if (es_palabra_inglesa(palabra_esp)[0] and es_palabra_espanola(palabra_ing)[0]):
                    row[col_espanol], row[col_ingles] = palabra_ing, palabra_esp
                    palabras_invertidas += 1
                    print(f"Palabras invertidas corregidas: {palabra_esp} ↔ {palabra_ing}")
            
            # Manejar casos de palabras faltantes
            if palabra_esp and not palabra_ing:
                # Solo tenemos palabra en español, inferir traducción al inglés
                if es_palabra_espanola(palabra_esp)[0]:
                    traduccion_inferida = inferir_traduccion(palabra_esp, True)
                    if traduccion_inferida:
                        row[col_ingles] = traduccion_inferida
                        traducciones_inferidas += 1
                        print(f"Traducción inferida para '{palabra_esp}': {traduccion_inferida}")
                    else:
                        palabras_sin_traducir.append((palabra_esp, "español"))
                        print(f"⚠️ No se pudo traducir la palabra española: {palabra_esp}")
                        
            elif palabra_ing and not palabra_esp:
                # Solo tenemos palabra en inglés, inferir traducción al español
                if es_palabra_inglesa(palabra_ing)[0]:
                    traduccion_inferida = inferir_traduccion(palabra_ing, False)
                    if traduccion_inferida:
                        row[col_espanol] = traduccion_inferida
                        traducciones_inferidas += 1
                        print(f"Traducción inferida para '{palabra_ing}': {traduccion_inferida}")
                    else:
                        palabras_sin_traducir.append((palabra_ing, "inglés"))
                        print(f"⚠️ No se pudo traducir la palabra inglesa: {palabra_ing}")
                        
            palabras_procesadas.append(row)
    
    # Preparar los nombres de columna para el archivo de salida
    nuevo_fieldnames = list(fieldnames)  # Copiar los campos existentes
    
    # Ya no añadimos columna de definición
    
    # Asegurarnos de tener columna de tipo si no existe
    if col_tipo is None:
        col_tipo = 'Tipo'
        nuevo_fieldnames.append(col_tipo)
    
    # Escribir el nuevo archivo CSV con definiciones
    with open(archivo_salida, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=nuevo_fieldnames)
        writer.writeheader()
        
        # Procesar cada palabra
        for i, row in enumerate(palabras_procesadas):
            palabra = row[col_espanol].strip()
            traduccion = row[col_ingles].strip()
            
            # Obtener tipo y ejemplo si están disponibles
            tipo = row.get(col_tipo, '').strip() if col_tipo in row else ''
            ejemplo = row.get(col_ejemplo, '').strip() if col_ejemplo in row and col_ejemplo else ''
            
            # Ya no procesamos definiciones
            print(f"Procesando {i+1}/{len(palabras_procesadas)}: {palabra}")
            
            # Asegurarse de que todas las columnas existan en la fila
            for field in nuevo_fieldnames:
                if field not in row:
                    row[field] = ''
            
            # Escribir fila con definición
            writer.writerow(row)
    
    # Mostrar resumen final de palabras duplicadas
    duplicados = {k: v for k, v in pares_duplicados.items() if len(v) > 1}
    if duplicados:
        print("\n⚠️ Pares de palabras duplicados encontrados:")
        for (esp, ing), ocurrencias in duplicados.items():
            print(f"- '{esp} - {ing}' aparece {len(ocurrencias)} veces")
        
        # Guardar duplicados en un archivo separado
        archivo_duplicados = archivo_salida.replace('.csv', '_duplicados.csv')
        with open(archivo_duplicados, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Español', 'Inglés', 'Número de ocurrencias'])
            for (esp, ing), ocurrencias in duplicados.items():
                writer.writerow([esp, ing, len(ocurrencias)])
        print(f"\nSe ha guardado la lista de palabras duplicadas en: {archivo_duplicados}")
        
    # Mostrar resumen final
    print(f"\n📊 Resumen del procesamiento:")
    print(f"- Total de palabras procesadas: {len(palabras_procesadas)}")
    print(f"- Traducciones inferidas: {traducciones_inferidas}")
    # Ya no mostramos definiciones agregadas
    print(f"- Pares de palabras duplicados: {len(duplicados)}")
    if palabras_invertidas > 0:
        print(f"- Pares de palabras invertidas: {palabras_invertidas}")
    
    # Guardar palabras con idioma incorrecto en un archivo separado
    if palabras_idioma_incorrecto:
        print("\n⚠️ Palabras en columna de idioma incorrecto:")
        for palabra, columna, idioma_detectado in palabras_idioma_incorrecto:
            print(f"- '{palabra}' está en columna {columna} pero parece {idioma_detectado}")
        
        archivo_idioma_incorrecto = archivo_salida.replace('.csv', '_idioma_incorrecto.csv')
        with open(archivo_idioma_incorrecto, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Palabra', 'Columna', 'Idioma Detectado'])
            writer.writerows(palabras_idioma_incorrecto)
            print(f"\nSe ha guardado la lista de palabras en idioma incorrecto en: {archivo_idioma_incorrecto}")
    
    # Mostrar resumen final de palabras sin traducir
    if palabras_sin_traducir:
        print("\n📝 Resumen de palabras sin traducir:")
        for palabra, idioma in palabras_sin_traducir:
            print(f"- {palabra} ({idioma})")
        
        # Guardar palabras sin traducir en un archivo separado
        archivo_sin_traducir = archivo_salida.replace('.csv', '_sin_traducir.csv')
        with open(archivo_sin_traducir, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Palabra', 'Idioma'])
            writer.writerows(palabras_sin_traducir)
            print(f"\nSe ha guardado la lista de palabras sin traducir en: {archivo_sin_traducir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Procesa un archivo CSV de palabras en español e inglés')
    parser.add_argument('archivo_entrada', help='Ruta del archivo CSV de entrada')
    parser.add_argument('archivo_salida', help='Ruta donde guardar el archivo CSV con definiciones')
    args = parser.parse_args()
    
    try:
        if not os.path.exists(args.archivo_entrada):
            raise FileNotFoundError(f"El archivo de entrada {args.archivo_entrada} no existe.")
        
        # Verificar que el archivo de entrada es un CSV
        if not args.archivo_entrada.lower().endswith('.csv'):
            raise ValueError(f"El archivo de entrada {args.archivo_entrada} debe ser un archivo CSV.")
        
        # Verificar que podemos escribir en el directorio de salida
        output_dir = os.path.dirname(args.archivo_salida) or '.'
        if not os.access(output_dir, os.W_OK):
            raise PermissionError(f"No hay permisos de escritura en el directorio de salida: {output_dir}")
        
        # Verificar que el archivo de salida tiene extensión .csv
        if not args.archivo_salida.lower().endswith('.csv'):
            raise ValueError(f"El archivo de salida {args.archivo_salida} debe tener extensión .csv")
        
        procesar_csv(args.archivo_entrada, args.archivo_salida)
        logging.info(f"Proceso completado. El archivo con definiciones se ha guardado en: {args.archivo_salida}")
        
    except Exception as e:
        logging.error(f"Error durante la ejecución: {str(e)}")
        sys.exit(1)
