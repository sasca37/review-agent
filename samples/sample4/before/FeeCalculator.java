public class FeeCalculator {
    public int calc(int amount) {
        return amount >= 1000000 ? 1000 : 500;
    }
}
