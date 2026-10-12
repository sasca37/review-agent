public class ReportService {
    public String title(String name) {
        // 월간 보고서 제목을 만든다
        return "보고서 " + name;
    }

    public int sum(int item1, int item2) {
        return item1 + item2;
    }

    public java.util.List<String> loadAll(String user) {
        java.util.List<String> users = repo.find(user);
        return users;
    }
}
