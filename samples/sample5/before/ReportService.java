public class ReportService {
    public String title(String name) {
        // 보고서 제목
        return "보고서 " + name;
    }

    public int sum(int item1, int item2) {
        return item1+item2;
    }

    public java.util.List<String> load(String user) {
        return repo.find(user);
    }
}
