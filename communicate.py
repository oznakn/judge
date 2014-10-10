import os
import select
import errno

_PIPE_BUF = getattr(select, 'PIPE_BUF', 512)


class OutputLimitExceeded(Exception):
    pass


def safe_communicate(proc, input, limit=None):
    if limit is None:
        limit = 10485760
    if proc.stdin:
        # Flush stdio buffer.  This might block, if the user has
        # been writing to .stdin in an uncontrolled fashion.
        proc.stdin.flush()
        if not input:
            proc.stdin.close()

    stdout = None # Return
    stderr = None # Return
    fd2file = {}
    fd2output = {}
    fd2length = {}

    poller = select.poll()

    def register_and_append(file_obj, eventmask):
        poller.register(file_obj.fileno(), eventmask)
        fd2file[file_obj.fileno()] = file_obj

    def close_unregister_and_remove(fd):
        poller.unregister(fd)
        fd2file[fd].close()
        fd2file.pop(fd)

    if proc.stdin and input:
        register_and_append(proc.stdin, select.POLLOUT)

    select_POLLIN_POLLPRI = select.POLLIN | select.POLLPRI
    if proc.stdout:
        register_and_append(proc.stdout, select_POLLIN_POLLPRI)
        fd2output[proc.stdout.fileno()] = stdout = []
        fd2length[proc.stdout.fileno()] = 0
    if proc.stderr:
        register_and_append(proc.stderr, select_POLLIN_POLLPRI)
        fd2output[proc.stderr.fileno()] = stderr = []
        fd2length[proc.stderr.fileno()] = 0

    input_offset = 0
    while fd2file:
        try:
            ready = poller.poll()
        except select.error, e:
            if e.args[0] == errno.EINTR:
                continue
            raise

        for fd, mode in ready:
            if mode & select.POLLOUT:
                chunk = input[input_offset:input_offset + _PIPE_BUF]
                try:
                    input_offset += os.write(fd, chunk)
                except OSError as e:
                    if e.errno == errno.EPIPE:
                        close_unregister_and_remove(fd)
                    else:
                        raise
                else:
                    if input_offset >= len(input):
                        close_unregister_and_remove(fd)
            elif mode & select_POLLIN_POLLPRI:
                data = os.read(fd, 4096)
                if not data:
                    close_unregister_and_remove(fd)
                fd2output[fd].append(data)
                fd2length[fd] += len(data)
                if fd2length[fd] > limit:
                    raise OutputLimitExceeded(['stderr', 'stdout'][proc.stdout.fileno() == fd],
                                              stdout and ''.join(stdout), stderr and ''.join(stderr))
            else:
                # Ignore hang up or errors.
                close_unregister_and_remove(fd)

    # All data exchanged.  Translate lists into strings.
    if stdout is not None:
        stdout = ''.join(stdout)
    if stderr is not None:
        stderr = ''.join(stderr)

    proc.wait()
    return stdout, stderr
