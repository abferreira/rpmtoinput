"""Run this, then press d-pad directions and watch what changes."""
import pygame, time

pygame.init()
pygame.joystick.init()
j = pygame.joystick.Joystick(0)
j.init()
print(f"Controller: {j.get_name()}  axes={j.get_numaxes()} btns={j.get_numbuttons()} hats={j.get_numhats()}")
print("Press d-pad buttons now. Ctrl+C to stop.\n")

prev_btns = [j.get_button(i) for i in range(j.get_numbuttons())]
prev_hat  = j.get_hat(0)

try:
    while True:
        pygame.event.pump()
        btns = [j.get_button(i) for i in range(j.get_numbuttons())]
        hat  = j.get_hat(0)

        for i, (old, new) in enumerate(zip(prev_btns, btns)):
            if old != new:
                print(f"  button[{i}] {'PRESSED' if new else 'released'}")

        if hat != prev_hat:
            print(f"  hat: {prev_hat} -> {hat}")

        prev_btns = btns
        prev_hat  = hat
        time.sleep(0.01)
except KeyboardInterrupt:
    pass
