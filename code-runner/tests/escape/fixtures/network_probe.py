import socket

def attempt(name, fn):
    try:
        fn()
        print("ESCAPED", name)
    except OSError:
        print("BLOCKED", name)

def tcp(host, port):
    def go():
        s = socket.create_connection((host, port), timeout=2)
        s.close()
    return go

attempt("tcp_public", tcp("1.1.1.1", 443))
attempt("tcp_docker_bridge", tcp("172.17.0.1", 22))
attempt("tcp_private", tcp("10.0.0.1", 443))
attempt("tcp_loopback_host_port", tcp("127.0.0.1", 2375))
attempt("metadata", tcp("169.254.169.254", 80))
attempt("dns", lambda: socket.getaddrinfo("example.com", 443))
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(b"x", ("8.8.8.8", 53))
    s.recvfrom(1)
attempt("udp", udp)
attempt("raw_socket", lambda: socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP))
attempt("packet_socket", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW))
