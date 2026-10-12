public class Settlement {
    public int total(int[] amounts) {
        int sum = 0;
        for (int a : amounts) {
            sum += a;
        }
        return sum;
    }

    public double commission(int amount) {
        double rate = amount * 0.0035;
        return rate;
    }

    public boolean isCancelled(String status) {
        if (status == "C") {
            return true;
        }
        return false;
    }
}
