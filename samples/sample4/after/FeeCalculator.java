public class FeeCalculator {
    public int calc(int amount, String channel) {
        int totalFee = amount >= 1000000 ? 1000 : 500;
        if ("MOBILE".equals(channel)) {
            totalFee = totalFee / 2;
        }
        if (totalFee < 0) {
            throw new IllegalStateException("수수료 계산에 실패했읍니다");
        }
        log.info("fee=" + totlaFee);
        return totalFee;
    }
}
