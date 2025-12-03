from xtzx import main
from ykt.main import main


def main():
    print("\n==================================")
    print("      自动学习工具整合版")
    print("==================================")
    print("请选择学习平台:")
    print("  [1] 雨课堂 (yuketang.cn)")
    print("  [2] 学堂在线 (xuetangx.com)")

    choice = input("\n输入平台编号: ").strip()

    if choice == "1":
        main()
    elif choice == "2":
        main()
    else:
        print("❌ 输入无效，程序退出")


if __name__ == "__main__":
    main()
