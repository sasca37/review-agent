public class LoanService {
    private final LoanRepository repo;

    public LoanService(LoanRepository repo) {
        this.repo = repo;
    }

    public int maxLoanAmount(int income) {
        return income * 3;
    }
}
