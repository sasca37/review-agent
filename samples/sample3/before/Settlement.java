public class Settlement {
    public int total(int[] amounts) {
        int sum = 0;
        for (int a : amounts) {
            sum += a;
        }
        return sum;
    }
}
