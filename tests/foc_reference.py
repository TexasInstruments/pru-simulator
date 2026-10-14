"""Independent, standard-library reference math for the FOC tests.

Nothing here imports the firmware ABI, the motor model or any generated file:
it re-derives inverse Park, TI ``SVGEN_runCom``, the speed ramp and the PMSM
equations so the tests compare two separate implementations.
"""
import math

SQRT3 = math.sqrt(3.0)
CENTER_CLAMP = SQRT3 / 4.0


def svgen_duties(alpha, beta):
    """TI SVGEN_runCom phase order with common-mode injection (per-unit)."""
    phases = (alpha, -alpha / 2.0 + SQRT3 / 2.0 * beta,
              -alpha / 2.0 - SQRT3 / 2.0 * beta)
    common = (max(phases) + min(phases)) / 2.0
    return tuple(0.5 + max(-CENTER_CLAMP, min(CENTER_CLAMP, phase - common))
                 for phase in phases)


def inverse_park(theta_turns, vd, vq):
    theta = 2.0 * math.pi * theta_turns
    return (vd * math.cos(theta) - vq * math.sin(theta),
            vd * math.sin(theta) + vq * math.cos(theta))


def vector_angle_from_duties(duties):
    """Voltage-vector angle (rad) seen in measured duties, Clarke of A/B/C."""
    mean = sum(duties) / 3.0
    alpha = duties[0] - mean
    beta = (duties[1] - duties[2]) / SQRT3
    return math.atan2(beta, alpha)


def ramped_angle_sequence(updates, speed_ref_pu, ramp_pu, initial_turns=0.0,
                          speed_pu=0.0, base_hz=1000.0, update_hz=16000.0):
    """Angle (turns) after each control update, ramping speed toward the ref."""
    angles = []
    angle = initial_turns
    for _ in range(updates):
        step = speed_ref_pu - speed_pu
        speed_pu = speed_ref_pu if abs(step) <= ramp_pu else speed_pu + math.copysign(ramp_pu, step)
        angle = (angle + speed_pu * base_hz / update_hz) % 1.0
        angles.append(angle)
    return angles


def pmsm_rk4(duties_per_period, params, period_s, steps_per_period=8):
    """Averaged-voltage PMSM reference integrated with RK4.

    ``duties_per_period`` is the list of (da, db, dc) applied for successive
    PWM periods. Returns (speed_rpm, theta_e, ialpha, ibeta) after each period.
    """
    r, l, flux = params["r"], params["l"], params["flux"]
    p, j, b, load, vdc = params["p"], params["j"], params["b"], params["load"], params["vdc"]

    def deriv(x, valpha, vbeta):
        ialpha, ibeta, omega, theta = x
        s, c = math.sin(theta), math.cos(theta)
        we = p * omega
        iq = -ialpha * s + ibeta * c
        torque = 1.5 * p * flux * iq
        return (
            (valpha - r * ialpha + flux * we * s) / l,
            (vbeta - r * ibeta - flux * we * c) / l,
            (torque - b * omega - load) / j,
            we,
        )

    x = (0.0, 0.0, 0.0, 0.0)
    h = period_s / steps_per_period
    out = []
    for duties in duties_per_period:
        mean = sum(duties) / 3.0
        valpha = (duties[0] - mean) * vdc
        vbeta = (duties[1] - duties[2]) * vdc / SQRT3
        for _ in range(steps_per_period):
            k1 = deriv(x, valpha, vbeta)
            k2 = deriv([a + h / 2 * k for a, k in zip(x, k1)], valpha, vbeta)
            k3 = deriv([a + h / 2 * k for a, k in zip(x, k2)], valpha, vbeta)
            k4 = deriv([a + h * k for a, k in zip(x, k3)], valpha, vbeta)
            x = tuple(a + h / 6 * (m + 2 * n + 2 * o + q)
                      for a, m, n, o, q in zip(x, k1, k2, k3, k4))
        out.append((x[2] * 60.0 / (2 * math.pi), x[3] % (2 * math.pi), x[0], x[1]))
    return out
