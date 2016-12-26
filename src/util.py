import os
import numpy as np
import cv2
import tensorflow as tf


def readFlowFile(filename):
    """
    readFlowFile read a flow file FILENAME into 2-band image IMG

    According to the c++ source code of Daniel Scharstein
    Contact: schar@middlebury.edu

    Author: Deqing Sun, Department of Computer Science, Brown University
    Contact: dqsun@cs.brown.edu
    Date: 2007-10-31 16:45:40 (Wed, 31 Oct 2006)

    Copyright 2007, Deqing Sun.

                        All Rights Reserved

    Permission to use, copy, modify, and distribute this software and its
    documentation for any purpose other than its incorporation into a
    commercial product is hereby granted without fee, provided that the
    above copyright notice appear in all copies and that both that
    copyright notice and this permission notice appear in supporting
    documentation, and that the name of the author and Brown University not be
    used in advertising or publicity pertaining to distribution of the software
    without specific, written prior permission.

    THE AUTHOR AND BROWN UNIVERSITY DISCLAIM ALL WARRANTIES WITH REGARD TO THIS
    SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
    FOR ANY PARTICULAR PURPOSE.  IN NO EVENT SHALL THE AUTHOR OR BROWN
    UNIVERSITY BE LIABLE FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR
    ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER
    IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
    OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
    """

    TAG_FLOAT = 202021.25  # check for this when READING the file

    # sanity check
    if len(filename) == 0:
        raise ValueError('readFlowFile: empty filename')

    try:
        idx = filename.index('.')
    except ValueError:
        raise ValueError('readFlowFile: extension required in filename %s' %
                         (filename))

    if filename[idx:] != '.flo':
        raise ValueError('readFlowFile: filename %s should have extension '
                         '\'.flo\'' % (filename))

    with open(filename, 'rb') as fid:
        tag = np.fromfile(fid, count=1, dtype=np.float32).item()
        width, height = np.fromfile(fid, count=2, dtype=np.int32)

        # sanity check
        if tag != TAG_FLOAT:
            raise ValueError('readFlowFile(%s): wrong tag (possibly due to'
                             ' big-endian machine?)' % (filename))

        if width < 1 or width > 99999:
            raise ValueError('readFlowFile(%s): illegal width %d' % (filename,
                                                                     width))

        if height < 1 or height > 99999:
            raise ValueError('readFlowFile(%s): illegal height %d' % (filename,
                                                                      height))

        nBands = 2

        # arrange into matrix form
        tmp = np.fromfile(fid, count=nBands*width*height, dtype=np.float32)
        img = tmp.reshape((height, width, nBands))

        return img


def draw_hsv(flow):
    h, w = flow.shape[:2]
    fx, fy = flow[:, :, 0], flow[:, :, 1]
    v, ang = cv2.cartToPolar(fx, fy)
    hsv = np.zeros((h, w, 3), np.uint8)
    hsv[..., 0] = ang*(180/np.pi/2)
    hsv[..., 1] = 255
    hsv[..., 2] = cv2.normalize(v, None, 0, 255, cv2.NORM_MINMAX)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    return bgr


def atan2_ocv(y, x):
    # constants
    DBL_EPSILON = 2.2204460492503131e-16
    atan2_p1 = 0.9997878412794807 * (180 / np.pi)
    atan2_p3 = -0.3258083974640975 * (180 / np.pi)
    atan2_p5 = 0.1555786518463281 * (180 / np.pi)
    atan2_p7 = -0.04432655554792128 * (180 / np.pi)

    ax, ay = tf.abs(x), tf.abs(y)
    c = tf.select(tf.greater_equal(ax, ay), tf.div(ay, ax + DBL_EPSILON),
                  tf.div(ax, ay + DBL_EPSILON))
    c2 = tf.square(c)
    angle = (((atan2_p7 * c2 + atan2_p5) * c2 + atan2_p3) * c2 + atan2_p1) * c
    angle = tf.select(tf.greater_equal(ax, ay), angle, 90.0 - angle)
    angle = tf.select(tf.less(x, 0.0), 180.0 - angle, angle)
    angle = tf.select(tf.less(y, 0.0), 360.0 - angle, angle)
    return angle


def normalize(tensor, a=0, b=1):
    return tf.div(tf.mul(tf.sub(tensor, tf.reduce_min(tensor)), b - a),
                  tf.sub(tf.reduce_max(tensor), tf.reduce_min(tensor)))


def cart_to_polar_ocv(x, y, angle_in_degrees=False):
    v = tf.sqrt(tf.add(tf.square(x), tf.square(y)))
    ang = atan2_ocv(y, x)
    scale = 1 if angle_in_degrees else np.pi / 180
    return v, tf.mul(ang, scale)


def draw_hsv_ocv(flow):
    fx, fy = flow[:, :, :, 0], flow[:, :, :, 1]
    v, ang = cart_to_polar_ocv(fx, fy)

    h = normalize(tf.mul(ang, 180 / np.pi))
    s = tf.ones_like(h)
    v = normalize(v)

    hsv = tf.pack([h, s, v], 3)
    rgb = tf.image.hsv_to_rgb(hsv) * 255

    return tf.cast(rgb, tf.uint8)


def check_snapshots(root_folder='', train=True):
    snapshots_folder = root_folder + 'snapshots/'
    logs_folder = root_folder + 'logs/'

    checkpoint = tf.train.latest_checkpoint(snapshots_folder)

    if train:
        resume = False
        start_iteration = 0

        if checkpoint:
            choice = ''
            while choice != 'y' and choice != 'n':
                print 'Snapshot file detected (' + checkpoint + \
                      ') would you like to resume? (y/n)'
                choice = raw_input().lower()

                if choice == 'y':
                    resume = checkpoint
                    start_iteration = int(checkpoint.split(snapshots_folder)
                                          [1][5:-5])
                    print 'resuming from iteration ' + str(start_iteration)
                else:
                    print 'removing old snapshots and logs, training from' \
                          ' scratch'
                    resume = False
                    for file in os.listdir(snapshots_folder):
                        if file == '.gitignore':
                            continue
                        os.remove(snapshots_folder + file)
                    for file in os.listdir(logs_folder + 'train/'):
                        if file == '.gitignore':
                            continue
                        os.remove(logs_folder + 'train/' + file)
                    for file in os.listdir(logs_folder + 'val/'):
                        if file == '.gitignore':
                            continue
                        os.remove(logs_folder + 'val/' + file)
        else:
            print "No snapshots found, training from scratch"

        return resume, start_iteration
    else:
        return checkpoint
