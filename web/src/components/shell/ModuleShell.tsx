import type { ReactNode } from "react";

/**
 * 模块级行框架 — 顶栏之下的一行:左侧栏槽 | (可选 header 条 / main / 可选右栏)。
 * 各模块 layout 自己渲染它,所以模块私有 Provider(Genny DataProvider、Andy as_of Ctx…)
 * 原位保留,不上提到全局。
 */
export function ModuleShell({
  sidebar,
  header,
  rail,
  children,
}: {
  sidebar: ReactNode;
  header?: ReactNode;
  rail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1">
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {header}
        <div className="flex min-h-0 flex-1">
          <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">{children}</main>
          {rail}
        </div>
      </div>
    </div>
  );
}
