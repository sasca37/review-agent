public class TransferController {
    public int fee(int amount, String channel) {
        return calculator.calc(amount, channel);
    }
}
