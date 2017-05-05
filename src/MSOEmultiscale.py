import tensorflow as tf
from src.graph_components import *
from src.utilities import *
from src.MSOEnet import MSOEnet
import time
import datetime
import numpy as np


class MSOEmultiscale(object):

    def __init__(self, config):
        # import config
        self.user_config = config['user']
        self.tf_config = config['tf']

        self.graph = tf.Graph()
        with self.graph.as_default():
            with tf.device('/gpu:' + str(self.user_config['gpu'])):
                # retrieve training and validation data
                self.data, input_shape, target_shape = \
                    data_layer('data_layer',
                               self.user_config['train_filename'],
                               self.user_config['batch_size'],
                               self.user_config['num_threads'])

                # set queue runner
                self.queue_runner = self.data['queue_runner']
                self.queue_runner.augment_data = True

                # create input and target placeholders for feeding in training
                # data, validation data, or test dat
                self.input = tf.placeholder(dtype=tf.float32,
                                            shape=[None] + input_shape,
                                            name='input')
                self.target = tf.placeholder(dtype=tf.float32,
                                             shape=[None] + target_shape,
                                             name='target')

                # build multi-scale pyramid
                self.output = self.build_pyramid('MSOEmultiscale', self.input)

                # attach loss to be minimized
                self.train_epe_squared = \
                    squared_epe('train_epe_squared', self.output, self.target) + \
                    tf.add_n(tf.get_collection('weight_regs'))

                # attach loss to be used for validation
                self.val_epe = \
                    epe('validation_epe', self.output, self.target)

                # attach loss segmented by speeds (for validation)
                self.num_segments = 8
                self.val_epes_segmented = \
                    epe_speedsegmented('validation_epe_segmented',
                                       self.output, self.target, self.num_segments)

                # attach summaries
                self.attach_summaries('summaries')

    # TODO: clean this up and refactor
    def attach_summaries(self, name):
        with tf.name_scope(name):
            # graph losses
            tf.summary.scalar('training epe squared', self.train_epe_squared)
            self.val_epe_placeholder = tf.placeholder(tf.float32)
            self.val_epe_summary = tf.summary.scalar('validation epe',
                                                 self.val_epe_placeholder,
                                                 collections=['val'])
            self.val_epe_segmented_summaries = []
            for i in range(self.num_segments):
                placeholder = tf.placeholder(tf.float32)
                self.val_epe_segmented_summaries.append({
                    'placeholder': placeholder,
                    'summary': tf.summary.scalar('validation epe segment ' + str(i),
                                                 placeholder,
                                                 collections=['val'])})

            # fetch shared weights for MSOEnet conv1
            with tf.variable_scope('MSOEnet_conv1', reuse=True):
                W_conv1 = tf.get_variable('weights')

            # visualize target and predicted flows
            tf.summary.image('flow predicted (normalized)',
                             flow_to_colour('flow_visualization',
                                            self.output[0:1]),
                             max_outputs=1)
            tf.summary.image('flow target (normalized)',
                             flow_to_colour('flow_visualization',
                                            self.target[0:1]),
                             max_outputs=1)
            tf.summary.image('flow predicted (clipped)',
                             flow_to_colour('flow_visualization',
                                            self.output[0:1], norm=False),
                             max_outputs=1)
            tf.summary.image('flow target (clipped)',
                             flow_to_colour('flow_visualization',
                                            self.target[0:1], norm=False),
                             max_outputs=1)

            # visualize input images
            tf.summary.image('image', self.input[0], max_outputs=2)

            # visualize filters
            viz0 = W_conv1[0, :, :, :, :]  # kernels for frame 1
            viz1 = W_conv1[1, :, :, :, :]  # kernels for frame 2
            grid0n = put_kernels_on_grid('kernel_visualization_norm',
                                         viz0, 8, 4)
            grid1n = put_kernels_on_grid('kernel_visualization_norm',
                                         viz1, 8, 4)
            grid0 = put_kernels_on_grid('kernel_visualization',
                                        viz0, 8, 4, norm=False)
            grid1 = put_kernels_on_grid('kernel_visualization',
                                        viz1, 8, 4, norm=False)
            tf.summary.image('filter conv1 0 norm', grid0n)
            tf.summary.image('filter conv1 1 norm', grid1n)
            tf.summary.image('filter conv1 0', grid0)
            tf.summary.image('filter conv1 1', grid1)

            # histogram of filters
            for i in range(32):
                drange = tf.reduce_max(W_conv1[:, :, :, :, i]) - \
                         tf.reduce_min(W_conv1[:, :, :, :, i])
                tf.summary.scalar('drange filter conv1 ' + str(i), drange)
                tf.summary.histogram('histogram filter conv1 ' + str(i),
                                     W_conv1[:, :, :, :, i])

            # visualize blurred inputs
            for scale in range(self.user_config['num_scales']):
                tf.summary.image('input_' + str(scale),
                                 self.multiscale_inputs[scale],
                                 max_outputs=1)

            # visualize queue usage
            data_queue = self.queue_runner
            data_queue_capacity = data_queue.batch_size * data_queue.n_threads
            tf.summary.scalar('queue saturation',
                              data_queue.queue.size() / data_queue_capacity)

            # merge summaries
            self.summaries = tf.summary.merge_all()

    def build_pyramid(self, name, input_layer, reuse=None):
        with tf.get_default_graph().name_scope(name):
            # contrast normalize input
            input_layer = contrast_norm('contrast_norm', input_layer)

            # initialize MSOEnet pyramid (batchx1xHxWx64)
            inputs = [input_layer]
            msoes = [MSOEnet('MSOEnet_0', inputs[0], reuse).output]

            # MSOEnet pyramid (big to small)
            num_scales = self.user_config['num_scales']
            for scale in range(1, num_scales):
                # downsample data (batchx2xhxwx1)
                inputs.append(blur_downsample3d('input_downsample_' +
                                                str(scale),
                                                inputs[scale-1], 5, 2,
                                                sigma=2))

                # create a MSOEnet and insert downsampled data (batchx1xhxwx64)
                small_msoe = MSOEnet('MSOEnet_' + str(scale), inputs[scale],
                                     reuse=True).output

                # upsample MSOEnet output to original input size (batchx1xHxWx64)
                msoe = bilinear_resample3d('MSOEnet_' + str(scale) + '_upsample',
                                           small_msoe, tf.shape(inputs[0])[2:4])

                msoes.append(msoe)

            # for visualizing the blurred inputs (temporary)
            self.multiscale_inputs = [input[:, 0] for input in inputs]

            # channel concat msoe outputs
            concatenated_msoes = channel_concat3d('MSOEnet_concat', msoes)

            # fourth convolution (1x3x3x64*num_scalesx64)
            conv4 = conv3d('MSOEnet_conv4', concatenated_msoes, 3, 1, 64, reuse)

            # activation
            h_conv4 = tf.nn.relu(conv4)

            # fifth convolution (flow out i.e. decode) (1x1x1x64x2)
            output = conv3d('MSOEnet_conv5', h_conv4, 1, 1, 2, reuse)

            # reshape (batch x H x W x 2)
            output = reshape('reshape', output,
                             [-1, tf.shape(output)[2],
                              tf.shape(output)[3], 2])

            return output

    def run_train(self):
        # for cleanliness
        iterations = self.user_config['iterations']
        lr = self.user_config['lr']
        snapshot_frequency = self.user_config['snapshot_frequency']
        print_frequency = self.user_config['print_frequency']
        validation_frequency = self.user_config['validation_frequency']
        run_id = self.user_config['run_id']

        with self.graph.as_default():
            with tf.device('/gpu:' + str(self.user_config['gpu'])):
                optimizer = tf.train.AdamOptimizer(learning_rate=lr)
                train_step = optimizer.minimize(self.train_epe_squared)

            """
            Train over iterations, printing loss at each one
            """
            saver = tf.train.Saver(max_to_keep=0, pad_step_number=16)
            with tf.Session(config=self.tf_config) as sess:

                # check snapshots
                resume, start_iteration = check_snapshots(run_id)

                # start summary writers
                summary_writer = tf.summary.FileWriter('logs/' + run_id, sess.graph)

                # start the tensorflow QueueRunners
                tf.train.start_queue_runners(sess=sess)

                # start the data queue runner's threads
                threads = self.queue_runner.start_threads(sess)

                if resume:
                    saver.restore(sess, resume)
                else:
                    sess.run(tf.global_variables_initializer())

                last_print = time.time()
                for i in range(start_iteration, iterations):
                    # retrieve training data
                    data = sess.run([self.data['train']['input'],
                                     self.data['train']['target']])
                    input = data[0]
                    target = data[1]

                    # run a train step
                    results = sess.run([train_step,
                                        self.train_epe_squared,
                                        self.summaries],
                                       feed_dict={
                                           self.input: input,
                                           self.target: target})

                    # print training information
                    if (i + 1) % print_frequency == 0:
                        time_diff = time.time() - last_print
                        it_per_sec = print_frequency / time_diff
                        remaining_it = iterations - i
                        eta = remaining_it / it_per_sec
                        print '(run ' + run_id + ')' + ' Iteration %d: ' \
                              'epe squared: %f lr: %f ' \
                              'iter per/s: %f ETA: %s' \
                              % (i + 1, results[1], lr, it_per_sec,
                                 str(datetime.timedelta(seconds=eta)))
                        summary_writer.add_summary(results[2], i + 1)
                        summary_writer.flush()
                        last_print = time.time()

                    # print validation information
                    if (i + 1) % validation_frequency == 0:
                        # retrieve validation data
                        val_input = self.data['validation']['input']
                        val_target = self.data['validation']['target']

                        num_validation = val_target.shape[0]
                        batch_size = self.user_config['batch_size']

                        print 'Validating ' + str(num_validation) + \
                            ' examples...'

                        # breaking up large validation data into chunks
                        # to evaluate separately
                        assert batch_size < num_validation
                        num_chunks = num_validation / batch_size
                        val_epe = 0
                        val_epes_segmented = np.zeros(self.num_segments)
                        for j in range(num_chunks):
                            start = j*batch_size
                            end = (j+1)*batch_size
                            results = sess.run([self.val_epe] +
                                               self.val_epes_segmented,
                                               feed_dict={
                                                   self.input:
                                                   val_input[start:end],
                                                   self.target:
                                                   val_target[start:end]})
                            val_epe += results[0]
                            val_epes_segmented += results[1:]

                        # evaluate the rest (if there are any)
                        if num_validation % batch_size != 0:
                            start = num_chunks*batch_size
                            results = sess.run([self.val_epe] +
                                               val_epes_segmented,
                                               feed_dict={
                                                   self.input:
                                                   val_input[start:],
                                                   self.target:
                                                   val_target[start:]})
                            val_epe += results[0]
                            val_epes_segmented += results[1:]
                            val_epe /= num_chunks + 1
                            val_epes_segmented /= num_chunks + 1
                        else:
                            val_epe /= num_chunks
                            val_epes_segmented /= num_chunks

                        print 'Validation epe: %f' % (val_epe)

                        summary = sess.run(self.val_epe_summary,
                                           feed_dict={
                                             self.val_epe_placeholder: val_epe})
                        summary_writer.add_summary(summary, i + 1)
                        summary_writer.flush()

                        for s in range(self.num_segments):
                            val_epe_summary = \
                                self.val_epe_segmented_summaries[s]['summary']
                            placeholder = \
                                self.val_epe_segmented_summaries[s]['placeholder']
                            summary = \
                                sess.run(val_epe_summary,
                                         feed_dict={placeholder:
                                                    val_epes_segmented[s]})
                            summary_writer.add_summary(summary, i + 1)
                            summary_writer.flush()

                    # save snapshot
                    if (i + 1) % snapshot_frequency == 0:
                        print 'Saving snapshot...'
                        try:
                            os.makedirs('snapshots/' + run_id)
                        except OSError:
                            if not os.path.isdir('snapshots/' + run_id):
                                raise
                        saver.save(sess, 'snapshots/' + run_id + '/iter', global_step=i+1)

    # TODO: revisit this code
    def run_test(self):
        with self.graph.as_default():
            # TODO: switch to tf.train.import_meta_graph
            saver = tf.train.Saver(max_to_keep=0)
            with tf.Session(config=self.tf_config) as sess:
                # load model
                model = check_snapshots(folder='final_model', train=False)
                saver.restore(sess, model)

                result = sess.run([self.output])[0]
                return result

    # TODO: revisit this code
    def save_model(self):
        with self.graph.as_default():
            saver = tf.train.Saver(max_to_keep=0)
            with tf.Session(config=self.tf_config) as sess:
                # load model
                model = check_snapshots(train=False)
                saver.restore(sess, model)
                saver.save(sess, 'final_model/MSOEnet.ckpt')
                for op in self.graph.get_operations():
                    print op.name
