// Rotary table kinematics stepper pulse time generation
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include "compiler.h"
#include "itersolve.h"
#include "trapq.h"

struct rotary_table_stepper {
    struct stepper_kinematics sk;
    double cx, cy, ay, az;
};

static struct coord
rotary_table_coord(struct stepper_kinematics *sk, struct move *m
                   , double move_time)
{
    struct rotary_table_stepper *rs = container_of(
        sk, struct rotary_table_stepper, sk);
    struct coord c = move_get_coord(m, move_time);
    double ra = c.a * (M_PI / 180.), rc = c.c * (M_PI / 180.);
    double sa = sin(ra), ca = cos(ra), sc = sin(rc), cc = cos(rc);
    double dx = c.x - rs->cx, dy = c.y - rs->cy;
    double qy = rs->cy + dx * sc + dy * cc - rs->ay, qz = c.z - rs->az;
    return (struct coord) {
        .x = rs->cx + dx * cc - dy * sc,
        .y = rs->ay + qy * ca - qz * sa,
        .z = rs->az + qy * sa + qz * ca };
}

static double
rotary_table_plus_calc_position(struct stepper_kinematics *sk, struct move *m
                                , double move_time)
{
    struct coord c = rotary_table_coord(sk, m, move_time);
    return c.x + c.y;
}

static double
rotary_table_minus_calc_position(struct stepper_kinematics *sk, struct move *m
                                 , double move_time)
{
    struct coord c = rotary_table_coord(sk, m, move_time);
    return c.x - c.y;
}

static double
rotary_table_x_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return rotary_table_coord(sk, m, move_time).x;
}

static double
rotary_table_y_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return rotary_table_coord(sk, m, move_time).y;
}

static double
rotary_table_z_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return rotary_table_coord(sk, m, move_time).z;
}

static double
rotary_table_a_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return move_get_coord(m, move_time).a;
}

static double
rotary_table_c_calc_position(struct stepper_kinematics *sk, struct move *m
                             , double move_time)
{
    return move_get_coord(m, move_time).c;
}

struct stepper_kinematics * __visible
rotary_table_stepper_alloc(char type, double cx, double cy
                           , double ay, double az)
{
    struct rotary_table_stepper *rs = malloc(sizeof(*rs));
    memset(rs, 0, sizeof(*rs));
    rs->cx = cx;
    rs->cy = cy;
    rs->ay = ay;
    rs->az = az;
    rs->sk.active_flags = AF_X | AF_Y | AF_Z | AF_A | AF_C;
    if (type == '+')
        rs->sk.calc_position_cb = rotary_table_plus_calc_position;
    else if (type == '-')
        rs->sk.calc_position_cb = rotary_table_minus_calc_position;
    else if (type == 'x')
        rs->sk.calc_position_cb = rotary_table_x_calc_position;
    else if (type == 'y')
        rs->sk.calc_position_cb = rotary_table_y_calc_position;
    else if (type == 'z')
        rs->sk.calc_position_cb = rotary_table_z_calc_position;
    else if (type == 'a') {
        rs->sk.calc_position_cb = rotary_table_a_calc_position;
        rs->sk.active_flags = AF_A;
    } else if (type == 'c') {
        rs->sk.calc_position_cb = rotary_table_c_calc_position;
        rs->sk.active_flags = AF_C;
    }
    return &rs->sk;
}
