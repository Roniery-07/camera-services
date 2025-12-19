import cv2
import numpy as np

# captura do vídeo (pode ser rtsp, arquivo, etc.)
cap = cv2.VideoCapture("rtsp://mmtx01.apagaofogo.eco.br:8554/D619-C0019-CA")

ret, prev = cap.read()
prev_gray = cv2.cvtColor(cv2.resize(prev, (320, 240)), cv2.COLOR_BGR2GRAY)

movendo = False
contador_parado = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    gray = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY)
    
    # diferença absoluta entre frames
    diff = cv2.absdiff(gray, prev_gray)
    
    # valor médio da diferença (custo baixo)
    mean_diff = diff.mean()
    
    # limiares (ajustar conforme teste)
    if mean_diff > 8:  
        movendo = True
        contador_parado = 0
    else:
        contador_parado += 1
        if contador_parado > 5:  # precisa ficar parado alguns frames
            movendo = False
    
    estado = "CÂMERA MEXENDO" if movendo else "CÂMERA PARADA"
    print(f"{estado} - diff média: {mean_diff:.2f}")
    
    prev_gray = gray

cap.release()
