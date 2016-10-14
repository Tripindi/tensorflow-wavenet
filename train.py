"""Training script for the WaveNet network on the VCTK corpus.

This script trains a network with the WaveNet using data from the VCTK corpus,
which can be freely downloaded at the following site (~10 GB):
http://homepages.inf.ed.ac.uk/jyamagis/page3/page58/page58.html
"""

from __future__ import print_function

import argparse
from datetime import datetime
import json
import os
import sys
import time

import tensorflow as tf
from tensorflow.python.client import timeline

from wavenet import WaveNetModel, AudioReader, optimizer_factory

NUM_GPUS = 1
BATCH_SIZE = 1
DATA_DIRECTORY = './VCTK-Corpus'
LOGDIR_ROOT = './logdir'
CHECKPOINT_EVERY = 50
NUM_STEPS = int(1e5)
LEARNING_RATE = 1e-3
WAVENET_PARAMS = './wavenet_params.json'
STARTED_DATESTRING = "{0:%Y-%m-%dT%H-%M-%S}".format(datetime.now())
SAMPLE_SIZE = 100000
L2_REGULARIZATION_STRENGTH = 0
SILENCE_THRESHOLD = 0.3
EPSILON = 0.001
MOMENTUM = 0.9
PS_HOSTS = ''
WORKER_HOSTS = ''
STANDALONE = 'standalone' 
JOB_NAME = STANDALONE

def get_arguments():
    parser = argparse.ArgumentParser(description='WaveNet example network')
    parser.add_argument('--batch_size', type=int, default=BATCH_SIZE,
                        help='How many wav files to process at once.')
    parser.add_argument('--data_dir', type=str, default=DATA_DIRECTORY,
                        help='The directory containing the VCTK corpus.')
    parser.add_argument('--store_metadata', type=bool, default=False,
                        help='Whether to store advanced debugging information '
                        '(execution time, memory consumption) for use with '
                        'TensorBoard.')
    parser.add_argument('--logdir', type=str, default=None,
                        help='Directory in which to store the logging '
                        'information for TensorBoard. '
                        'If the model already exists, it will restore '
                        'the state and will continue training. '
                        'Cannot use with --logdir_root and --restore_from.')
    parser.add_argument('--logdir_root', type=str, default=None,
                        help='Root directory to place the logging '
                        'output and generated model. These are stored '
                        'under the dated subdirectory of --logdir_root. '
                        'Cannot use with --logdir.')
    parser.add_argument('--restore_from', type=str, default=None,
                        help='Directory in which to restore the model from. '
                        'This creates the new model under the dated directory '
                        'in --logdir_root. '
                        'Cannot use with --logdir.')
    parser.add_argument('--checkpoint_every', type=int, default=CHECKPOINT_EVERY,
                        help='How many steps to save each checkpoint after')
    parser.add_argument('--num_steps', type=int, default=NUM_STEPS,
                        help='Number of training steps.')
    parser.add_argument('--learning_rate', type=float, default=LEARNING_RATE,
                        help='Learning rate for training.')
    parser.add_argument('--wavenet_params', type=str, default=WAVENET_PARAMS,
                        help='JSON file with the network parameters.')
    parser.add_argument('--sample_size', type=int, default=SAMPLE_SIZE,
                        help='Concatenate and cut audio samples to this many '
                        'samples.')
    parser.add_argument('--l2_regularization_strength', type=float,
                        default=L2_REGULARIZATION_STRENGTH,
                        help='Coefficient in the L2 regularization. '
                        'Disabled by default')
    parser.add_argument('--silence_threshold', type=float,
                        default=SILENCE_THRESHOLD,
                        help='Volume threshold below which to trim the start '
                        'and the end from the training set samples.')
    parser.add_argument('--optimizer', type=str, default='adam',
                        choices=optimizer_factory.keys(),
                        help='Select the optimizer specified by this option.')
    parser.add_argument('--momentum', type=float,
                        default=MOMENTUM, help='Specify the momentum to be '
                        'used by sgd or rmsprop optimizer. Ignored by the '
                        'adam optimizer.')
    parser.add_argument('--num_gpus', type=int, default=NUM_GPUS,
                        help='number of gpus to use')
    parser.add_argument('--random_crop', type=bool, default=False,
                        help='Whether to crop randomly')
    parser.add_argument('--ps_hosts', type=str, default=PS_HOSTS,
                        help='Comma-separated list of hostname:port pairs')
    parser.add_argument('--worker_hosts', type=str, default=WORKER_HOSTS,
                        help='Comma-separated list of hostname:port pairs')
    parser.add_argument('--job_name', type=str, default=JOB_NAME,
                        help="One of 'ps', 'worker', 'standalone'")
    parser.add_argument('--task_index', type=int, default=0,
                        help="Index of task within the job")
    return parser.parse_args()


def save(saver, sess, logdir, step):
    model_name = 'model.ckpt'
    checkpoint_path = os.path.join(logdir, model_name)
    print('Storing checkpoint to {} ...'.format(logdir), end="")
    sys.stdout.flush()

    if not os.path.exists(logdir):
        os.makedirs(logdir)

    saver.save(sess, checkpoint_path, global_step=step)
    print(' Done.')


def load(saver, sess, logdir):
    print("Trying to restore saved checkpoints from {} ...".format(logdir),
          end="")

    ckpt = tf.train.get_checkpoint_state(logdir)
    if ckpt:
        print("  Checkpoint found: {}".format(ckpt.model_checkpoint_path))
        global_step = int(ckpt.model_checkpoint_path
                          .split('/')[-1]
                          .split('-')[-1])
        print("  Global step was: {}".format(global_step))
        print("  Restoring...", end="")
        saver.restore(sess, ckpt.model_checkpoint_path)
        print(" Done.")
        return global_step
    else:
        print(" No checkpoint found.")
        return None


def get_default_logdir(logdir_root):
    logdir = os.path.join(logdir_root, 'train', STARTED_DATESTRING)
    return logdir


def validate_directories(args):
    """Validate and arrange directory related arguments."""

    # Validation
    if args.logdir and args.logdir_root:
        raise ValueError("--logdir and --logdir_root cannot be "
                         "specified at the same time.")

    if args.logdir and args.restore_from:
        raise ValueError(
            "--logdir and --restore_from cannot be specified at the same "
            "time. This is to keep your previous model from unexpected "
            "overwrites.\n"
            "Use --logdir_root to specify the root of the directory which "
            "will be automatically created with current date and time, or use "
            "only --logdir to just continue the training from the last "
            "checkpoint.")

    # Arrangement
    logdir_root = args.logdir_root
    if logdir_root is None:
        logdir_root = LOGDIR_ROOT

    logdir = args.logdir
    if logdir is None:
        logdir = get_default_logdir(logdir_root)
        print('Using default logdir: {}'.format(logdir))

    restore_from = args.restore_from
    if restore_from is None:
        # args.logdir and args.restore_from are exclusive,
        # so it is guaranteed the logdir here is newly created.
        restore_from = logdir

    return {
        'logdir': logdir,
        'logdir_root': args.logdir_root,
        'restore_from': restore_from
    }

def create_optimizer(args):
    optimizer = optimizer_factory[args.optimizer](
        learning_rate=args.learning_rate,
        momentum=args.momentum)    
    return optimizer

def create_network(args,wavenet_params,audio_batch):
    # Create network.
    net = WaveNetModel(
        batch_size=args.batch_size,
        dilations=wavenet_params["dilations"],
        filter_width=wavenet_params["filter_width"],
        residual_channels=wavenet_params["residual_channels"],
        dilation_channels=wavenet_params["dilation_channels"],
        skip_channels=wavenet_params["skip_channels"],
        quantization_channels=wavenet_params["quantization_channels"],
        use_biases=wavenet_params["use_biases"],
        scalar_input=wavenet_params["scalar_input"],
        initial_filter_width=wavenet_params["initial_filter_width"])
    if args.l2_regularization_strength == 0:
        args.l2_regularization_strength = None
    loss = net.loss(audio_batch, args.l2_regularization_strength)    

    return loss

def build_singlegpu(args,wavenet_params,audio_batch):
    loss = create_network(args,wavenet_params,audio_batch)
    optimizer = create_optimizer(args)
    trainable = tf.trainable_variables()
    optim = optimizer.minimize(loss, var_list=trainable)
    summary_op = tf.merge_all_summaries()
    return loss, optim, summary_op    

def build_multigpu(args,wavenet_params,audio_batch):
    tower_grads = []
    tower_losses = []
    optimizer = create_optimizer(args)
    
    for device_index in xrange(args.num_gpus):
        with tf.device('/gpu:%d' % device_index):
            with tf.name_scope('tower_%d' % device_index) as scope:
                loss = create_network(args,wavenet_params,audio_batch)
                trainable = tf.trainable_variables()
                grads = optimizer.compute_gradients(loss, var_list=trainable)
                tower_losses.append(loss)
                tower_grads.append(grads)
                summaries = tf.get_collection(tf.GraphKeys.SUMMARIES, scope)
                tf.get_variable_scope().reuse_variables()
    
    loss = tf.reduce_mean(tower_losses)

    # average gradients
    average_grads = []
    for grad_and_vars in zip(*tower_grads):
        grads = []
        for g,_ in grad_and_vars:
            if g is None:
                continue
            expanded_g = tf.expand_dims(g,0)
            grads.append(expanded_g)
        
        if len(grads) == 0:
            average_grads.append((None,v))
            continue
        grad = tf.concat(0,grads)
        grad = tf.reduce_mean(grad,0)

        v = grad_and_vars[0][1]
        grad_and_var = (grad,v)
        average_grads.append(grad_and_var)

    # Pass gradients to optimizer. 
    optim = optimizer.apply_gradients(average_grads)

    # Summary op
    summary_op = tf.merge_summary(summaries)

    return loss, optim, summary_op

def create_inputs(args,wavenet_params):
    # Create coordinator.
    coord = tf.train.Coordinator()

    # Load raw waveform from VCTK corpus.
    with tf.name_scope('create_inputs'):
        # Allow silence trimming to be skipped by specifying a threshold near
        # zero.
        silence_threshold = args.silence_threshold if args.silence_threshold > \
                                                      EPSILON else None
        reader = AudioReader(
            args.data_dir,
            coord,
            sample_rate=wavenet_params['sample_rate'],
            sample_size=args.sample_size,
            random_crop=args.random_crop,
            silence_threshold=args.silence_threshold)
        audio_batch = reader.dequeue(args.batch_size)

    return coord, audio_batch, reader

def main():
    args = get_arguments()

    try:
        directories = validate_directories(args)
    except ValueError as e:
        print("Some arguments are wrong:")
        print(str(e))
        return

    logdir = directories['logdir']
    logdir_root = directories['logdir_root']
    restore_from = directories['restore_from']

    # Even if we restored the model, we will treat it as new training
    # if the trained model is written into an arbitrary location.
    is_overwritten_training = logdir != restore_from

    with open(args.wavenet_params, 'r') as f:
        wavenet_params = json.load(f)

    if args.job_name != STANDALONE:
        distributed(args,wavenet_params,logdir)
    else:
        standalone(args,wavenet_params,logdir,logdir_root,restore_from,is_overwritten_training)


def distributed(args,wavenet_params,logdir):
    ps_hosts = args.ps_hosts.split(",")
    worker_hosts = args.worker_hosts.split(",")
    cluster = tf.train.ClusterSpec({"ps": ps_hosts, "worker": worker_hosts})
    server = tf.train.Server(cluster,
        job_name=args.job_name,
        task_index=args.task_index)

    if args.job_name == "ps":
        server.join()
    elif args.job_name == "worker":
        coord, audio_batch, reader = create_inputs(args,wavenet_params)

        with tf.device(tf.train.replica_device_setter(
            worker_device="/job:worker/task:%d" % args.task_index,
            cluster=cluster)):

            # Build graph
            loss = create_network(args,wavenet_params,audio_batch)
            optimizer = create_optimizer(args)
            global_step = tf.Variable(0)
            num_workers = len(worker_hosts)

            # Setup sync replicas optimizer
            sync_rep_opt = tf.train.SyncReplicasOptimizer(optimizer, replicas_to_aggregate=num_workers,
                replica_id=args.task_index, total_num_replicas=num_workers)

            # Setup operators
            train_op = sync_rep_opt.minimize(loss, global_step=global_step)
            init_token_op = sync_rep_opt.get_init_tokens_op()

            # Queue runner
            chief_queue_runner = sync_rep_opt.get_chief_queue_runner()

            # Summary op
            summary_op = tf.merge_all_summaries()

            # Init op
            init_op = tf.initialize_all_variables()

            # saver
            saver = tf.train.Saver()

        is_chief = (args.task_index == 0)

        sv = tf.train.Supervisor(
            is_chief=is_chief,
            init_op=init_op,
            summary_op=summary_op,
            saver=saver,
            global_step=global_step,
            logdir=logdir
        )

        with sv.managed_session(server.target) as sess:
            try:
                threads = tf.train.start_queue_runners(sess=sess, coord=coord)
                reader.start_threads(sess)

                if is_chief:
                    sv.start_queue_runners(sess, [chief_queue_runner])
                    sess.run(init_token_op)

                    # Set up logging for TensorBoard.
                    writer = tf.train.SummaryWriter(logdir)
                    writer.add_graph(tf.get_default_graph())

                total_duration = 0
                step = 0

                while not sv.should_stop() and step <= args.num_steps:
                    start_time = time.time()

                    sys.stdout.flush()
                    summary, loss_value, step, _ = sess.run([summary_op, loss, global_step, train_op])

                    if is_chief:
                        writer.add_summary(summary, step)

                    duration = time.time() - start_time
                    print('step {:d} - loss = {:.3f}, ({:.3f} sec/step)'
                        .format(step, loss_value, duration))

            except KeyboardInterrupt:
                # Introduce a line break after ^C is displayed so save message
                # is on its own line.
                print()
            finally:
                save(saver, sess, logdir, step)

                coord.request_stop()
                coord.join(threads)

                sv.stop()

def get_tower(args,wavenet_params,audio_batch):
    if args.num_gpus > 1:
        builder = build_multigpu
    else:
        builder = build_singlegpu
    return builder(args,wavenet_params,audio_batch)


def standalone(args,wavenet_params,logdir,logdir_root,restore_from,is_overwritten_training):
    coord, audio_batch, reader = create_inputs(args,wavenet_params)

    loss, optim, summary_op = get_tower(args,wavenet_params,audio_batch)

    # Set up logging for TensorBoard.
    writer = tf.train.SummaryWriter(logdir)
    writer.add_graph(tf.get_default_graph())
    run_metadata = tf.RunMetadata()

    # Set up session
    sess = tf.Session(config=tf.ConfigProto(log_device_placement=False,allow_soft_placement=True))
    init = tf.initialize_all_variables()
    sess.run(init)

    # Saver for storing checkpoints of the model.
    saver = tf.train.Saver(var_list=tf.trainable_variables())

    try:
        saved_global_step = load(saver, sess, restore_from)
        if is_overwritten_training or saved_global_step is None:
            # The first training step will be saved_global_step + 1,
            # therefore we put -1 here for new or overwritten trainings.
            saved_global_step = -1

    except:
        print("Something went wrong while restoring checkpoint. "
              "We will terminate training to avoid accidentally overwriting "
              "the previous model.")
        raise

    threads = tf.train.start_queue_runners(sess=sess, coord=coord)
    reader.start_threads(sess)

    try:
        last_saved_step = saved_global_step
        for step in range(saved_global_step + 1, args.num_steps):
            start_time = time.time()
            if args.store_metadata and step % 50 == 0:
                # Slow run that stores extra information for debugging.
                print('Storing metadata')
                run_options = tf.RunOptions(
                    trace_level=tf.RunOptions.FULL_TRACE)
                summary, loss_value, _ = sess.run(
                    [summary_op, loss, optim],
                    options=run_options,
                    run_metadata=run_metadata)
                writer.add_summary(summary, step)
                writer.add_run_metadata(run_metadata,
                                        'step_{:04d}'.format(step))
                tl = timeline.Timeline(run_metadata.step_stats)
                timeline_path = os.path.join(logdir, 'timeline.trace')
                with open(timeline_path, 'w') as f:
                    f.write(tl.generate_chrome_trace_format(show_memory=True))
            else:
                summary, loss_value, _ = sess.run([summary_op, loss, optim])
                writer.add_summary(summary, step)

            duration = time.time() - start_time
            print('step {:d} - loss = {:.3f}, ({:.3f} sec/step)'
                  .format(step, loss_value, duration))

            if step % args.checkpoint_every == 0:
                save(saver, sess, logdir, step)
                last_saved_step = step

    except KeyboardInterrupt:
        # Introduce a line break after ^C is displayed so save message
        # is on its own line.
        print()
    finally:
        if step > last_saved_step:
            save(saver, sess, logdir, step)
        coord.request_stop()
        coord.join(threads)


if __name__ == '__main__':
    main()
