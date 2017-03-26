import tensorflow as tf
from src.MSOEmultiscale import MSOEmultiscale

# config
config_proto = tf.ConfigProto()
config_proto.gpu_options.allow_growth = True
config_proto.allow_soft_placement = True
config_proto.log_device_placement = False
# config.intra_op_parallelism_threads = 1
my_config = {}
my_config['train_filename'] = \
    '/home/mtesfald/Datasets/FlyingChairs/FlyingChairs_release/data'
my_config['batch_size'] = 4
my_config['iterations'] = 600000
my_config['snapshot_frequency'] = 20
my_config['print_frequency'] = 10
my_config['validation_frequency'] = 50
my_config['lr'] = 1.2e-2
my_config['num_threads'] = 6
my_config['num_scales'] = 5
my_config['gpu'] = 0
my_config['run_id'] = '0'

net = MSOEmultiscale(config={'tf': config_proto,
                             'user': my_config})
net.run_train()
