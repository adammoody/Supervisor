from mpi4py import MPI
import ctypes, time

from cStringIO import StringIO

from collections import deque
import random

class Timer:

    def __init__(self, fname=None):
        if fname == None:
            self.out = None
        else:
            self.out = open(fname, 'w')


    def start(self):
        self.t = time.time()

    def end(self, msg):
        duration = time.time() - self.t
        line = "{},{},{},{}".format(msg, self.t, time.time(), duration)
        if self.out != None:
            self.out.write("{}\n".format(line))
        else:
            print(line)

    def close(self):
        if self.out != None:
            self.out.close()

class ModelMetaData:
    def __init__(self, rank, score, size):
        self.rank = rank
        self.size = size
        self.score = score

    def __str__(self):
        return "ModelMetaData[rank: {}, size: {}, score: {}]".format(self.rank,
                                                                     self.size,
                                                                     self.score)


class ModelData:

    def __init__(self, data):
        """
        data - ctypes c_double array of score, size, score, size ...
        ordered by rank
        """
        self.items = []
        self.max_ranks = []
        max_score = float('-inf')
        rank = 0
        for i in xrange(0,len(data), 2):
            score = data[i]
            size = data[i + 1]
            md = ModelMetaData(rank, score, size)
            self.items.append(md)
            if score > max_score:
                self.max_ranks = [rank]
                max_score = score
            elif score == max_score:
                self.max_ranks.append(rank)

            rank += 1

    def max(self):
        return self.items[self.max_ranks[0]]

    def get_data(self, rank):
        return self.items[rank]


class PBTDataSpaces:

    def __init__(self):
        self.lib = ctypes.cdll.LoadLibrary("./libpbt_ds.so")
        # different mpi implementation use different types for
        # MPI_Comm, this determines which type to use
        if MPI._sizeof(MPI.Comm) == ctypes.sizeof(ctypes.c_int):
            self.mpi_comm_type = ctypes.c_int
        else:
            self.mpi_comm_type = ctypes.c_void_p

        self.mpi_comm_self = self.make_comm_arg(MPI.COMM_SELF)
        self.mpi_comm_world = self.make_comm_arg(MPI.COMM_WORLD)
        self.rank = MPI.COMM_WORLD.Get_rank()
        self.world_size = MPI.COMM_WORLD.Get_size()

    def make_comm_arg(self, comm):
        comm_ptr = MPI._addressof(comm)
        comm_val = self.mpi_comm_type.from_address(comm_ptr)
        return comm_val

    def init_ds(self):
        self.lib.pbt_ds_init(ctypes.c_int(self.world_size), self.mpi_comm_world)

    def init_scores(self):
        self.lib.pbt_ds_define_score_dim(self.world_size)
        self.lib.pbt_ds_put_score(self.rank, ctypes.c_double(0.0),
                            ctypes.c_double(0.0), self.mpi_comm_self)

    def put_weights(self, pickled_weights, score):
        weights_size = len(pickled_weights)
        self.lib.pbt_ds_put_score(self.rank, ctypes.c_double(score),
                                ctypes.c_double(weights_size), self.mpi_comm_self)
        self.lib.pbt_ds_put_weights(self.rank, pickled_weights, weights_size, self.mpi_comm_self)

    def get_weights(self, model_data):
        size = int(model_data.size)
        data = ctypes.create_string_buffer(size)
        self.lib.pbt_ds_get_weights(model_data.rank, data, size, self.mpi_comm_self)
        return StringIO(data)

    def get_model_data(self):
        size = 2 * self.world_size
        # do NOT do "ctypes.c_double * 2 * self.world_size"!
        # that creates something else entirely!
        data = (ctypes.c_double * size)()
        self.lib.pbt_ds_get_all_scores(self.world_size, data, self.mpi_comm_self)
        return ModelData(data)

    def finalize(self):
        self.lib.pbt_ds_finalize()


class MsgType:
    LOCKED, UNLOCKED, ACQUIRE_READ_LOCK, RELEASE_READ_LOCK, ACQUIRE_WRITE_LOCK, RELEASE_WRITE_LOCK, GET_BEST_SCORE, PUT_SCORE, DONE = range(9)


class Tags:
    REQUEST, ACK, SCORE = range(3)


class PBTClient:

    def __init__(self, comm, dest):
        self.comm = comm
        self.rank = comm.Get_rank()
        self.dest = dest

    def acquire_read_lock(self, for_rank):
        msg = {'type' : MsgType.ACQUIRE_READ_LOCK, 'rank' : for_rank}
        status = MPI.Status()
        #print("{} requesting read lock: {}".format(self.rank, msg))
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)
        # wait for acknowledgement of lock
        self.comm.recv(source=self.dest, tag=Tags.ACK, status=status)
        #print("{} acquired read lock".format(self.rank))

    def release_read_lock(self, for_rank):
        msg = {'type' : MsgType.RELEASE_READ_LOCK, 'rank' : for_rank}
        status = MPI.Status()
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)
        # wait for acknowledgement of lock release
        self.comm.recv(source=self.dest, tag=Tags.ACK, status=status)

    def release_write_lock(self, for_rank):
        msg = {'type' : MsgType.RELEASE_WRITE_LOCK, 'rank' : for_rank}
        status = MPI.Status()
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)
        # wait for acknowledgement of lock release
        self.comm.recv(source=self.dest, tag=Tags.ACK, status=status)


    def get_best_score(self, lock_weights=True):
        msg = {'type' : MsgType.GET_BEST_SCORE, 'lock_weights' : lock_weights}
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)
        status = MPI.Status()
        if lock_weights:
            self.comm.recv(source=self.dest, tag=Tags.ACK, status=status)
            #print{"{} acquired weights lock".format(self.rank))

        score_rank = self.comm.recv(source=self.dest, tag=Tags.SCORE, status=status)
        return score_rank

    def put_score(self, score, lock_weights=True):
        msg = {'type' : MsgType.PUT_SCORE, 'score' : score,
               'lock_weights' : lock_weights}
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)
        # don't return until the score has actually been put
        self.comm.recv(source=self.dest, tag=Tags.ACK)
        status = MPI.Status()
        if lock_weights:
            self.comm.recv(source=self.dest, tag=Tags.ACK, status=status)
            #print{"{} acquired weights lock".format(self.rank))

    def done(self):
        msg = {'type' : MsgType.DONE}
        self.comm.send(msg, dest=self.dest, tag=Tags.REQUEST)


class DataStoreLock:

    def __init__(self, comm, source, target):
        self.source = source
        self.target = target
        self.comm = comm

    def lock(self):
        #print{"Ack for lock '{}' lock from {}".format(self.locked_obj, self.target))
        # send the acknowledgement of the lock back to target
        self.comm.send(MsgType.LOCKED, dest=self.target, tag=Tags.ACK)

    def unlock(self):
        #print{"Ack for unlock '{}' lock from {}".format(self.locked_obj, self.target))
        self.comm.send(MsgType.UNLOCKED, dest=self.target, tag=Tags.ACK)


class DataStoreLockManager:

    def __init__(self, comm, rank):
        self.rank = rank
        self.comm = comm
        self.readers = {}
        self.queued_readers = deque()
        self.queued_writers = deque()
        self.writer = None

    def read_lock(self, requesting_rank):
        # if writer, then queue the read
        # else add DataStoreLock to readers, and call locks
        lock = DataStoreLock(self.comm, self.rank, requesting_rank)
        if self.writer != None:
            self.queued_readers.append(lock)
        else:
            self.readers[requesting_rank] = lock
            lock.lock()

    def read_unlock(self, requesting_rank):
        # look up Lock in readers and send unlock and remove from readers
        # if readers is empty, then lock first queued writer and set self.write
        lock = self.readers.pop(requesting_rank)
        lock.unlock()
        if len(self.readers) == 0 and len(self.queued_writers) > 0:
            self.writer = self.queued_writers.popleft()
            self.writer.lock()

    def write_lock(self, requesting_rank):
        # if no readers and self.writer = None then set self.writer
        # else add to queued writers
        lock = DataStoreLock(self.comm, self.rank, requesting_rank)
        if len(self.readers) == 0 and self.writer == None:
            self.writer = lock
            lock.lock()
        else:
            self.queued_writers.append(lock)

    def write_unlock(self, requesting_rank):
        # unlock self.writer (shouldn't be None!), set to None
        # if queued readers then lock those, else check queued writers
        # and lock first for those
        if self.writer.target != requesting_rank:
            print("bad write unlock")

        self.writer.unlock()
        self.writer = None
        if len(self.queued_readers) > 0:
            for lock in self.queued_readers:
                lock.lock()
                self.readers[lock.target] = lock
            self.queued_readers = deque()

        elif len(self.queued_writers) > 0:
            self.writer = queued_writers.popleft()
            self.writer.lock()


class PBTMetaDataStore:

    def __init__(self, comm):
        self.locks = {}
        self.comm = comm
        self.rank = self.comm.Get_rank()
        self.scores = {}
        for i in range(self.comm.Get_size()):
            if i != self.rank:
                self.locks[i] = DataStoreLockManager(self.comm, self.rank)

    def acquire_read_lock(self, requesting_rank, key):
        #print("{} acquiring read lock for {}".format(requesting_rank, key))
        lock_manager = self.locks[key]
        lock_manager.read_lock(requesting_rank)

    def release_read_lock(self, requesting_rank, key):
        #print("{} releasing read lock for {}".format(requesting_rank, key))
        lock_manager = self.locks[key]
        lock_manager.read_unlock(requesting_rank)

    def acquire_write_lock(self, requesting_rank, key):
        #print("{} acquiring write lock for {}".format(requesting_rank, key))
        lock_manager = self.locks[key]
        lock_manager.write_lock(requesting_rank)

    def release_write_lock(self, requesting_rank, key):
        #print("{} releasing write lock for {}".format(requesting_rank, key))
        lock_manager = self.locks[key]
        lock_manager.write_unlock(requesting_rank)

    def get_best_score(self):
        # use min, expecting val_loss
        mins = []
        val = float('inf')
        for k in self.scores:
            score = self.scores[k]
            if score < val:
                mins = []
                val = score
                mins.append(k)
            elif score == val:
                mins.append(k)

        return (random.choice(mins), val)

    def put_score(self, putting_rank, score):
        #print("Putting score {},{}".format(putting_rank, score))
        self.scores[putting_rank] = score
        self.comm.send(MsgType.PUT_SCORE, tag=Tags.ACK, dest=putting_rank)

    def run(self):
        status = MPI.Status()
        live_ranks = self.comm.Get_size() - 1
        while live_ranks > 0:
            msg = self.comm.recv(source=MPI.ANY_SOURCE, tag=Tags.REQUEST, status=status)
            source = status.Get_source()
            msg_type = msg['type']

            if msg_type == MsgType.ACQUIRE_READ_LOCK:
                msg_rank = msg['rank']
                self.acquire_read_lock(source, msg_rank)

            elif msg_type == MsgType.RELEASE_READ_LOCK:
                msg_rank = msg['rank']
                self.release_read_lock(source, msg_rank)

            elif msg_type == MsgType.RELEASE_WRITE_LOCK:
                msg_rank = msg['rank']
                self.release_write_lock(source, msg_rank)

            elif msg_type == MsgType.PUT_SCORE:
                score = msg['score']
                lock_weights = msg['lock_weights']
                self.put_score(source, score)
                if lock_weights:
                    self.acquire_write_lock(source, source)

            elif msg_type == MsgType.GET_BEST_SCORE:
                rank, score = self.get_best_score()
                lock_weights = msg['lock_weights']
                if lock_weights:
                    key = "weights{}".format(rank)
                    self.acquire_read_lock(source, rank)

                self.comm.send((rank,score), dest=source, tag=Tags.SCORE)

            elif msg_type == MsgType.DONE:
                live_ranks -= 1

        print("Done")
