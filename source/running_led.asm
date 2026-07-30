; running led
start:
    ldi r30, 1
    loop endloop, 19
    lsl r30, r30, 1
endloop:
    qba start

