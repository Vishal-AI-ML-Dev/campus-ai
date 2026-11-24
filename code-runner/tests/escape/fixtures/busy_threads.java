public class Main {
    public static void main(String[] args) throws Exception {
        for (int i = 0; i < 64; i++) {
            Thread t = new Thread(() -> { long x = 0; while (true) x++; });
            t.setDaemon(false);
            t.start();
        }
        while (true) Thread.onSpinWait();
    }
}
